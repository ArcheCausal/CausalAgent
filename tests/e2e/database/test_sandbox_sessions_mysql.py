"""显式执行的真实 MySQL 验收；仅接受本次内部网络上的全新隔离空库。

本文件测试真实 migration、SQL 竞争与 fencing，不创建远端沙盒。
运行需 SANDBOX_DATABASE_ACCEPTANCE=1、sandbox-lifecycle-db-* 主机名、
固定 sandbox_lifecycle_acceptance 数据库和容器内临时密钥文件路径。
不使用替代连接或替代 SQL 执行器；结束后保留验收数据库和记录。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import importlib
import json
import os
from pathlib import Path
import secrets
import threading
import time
from uuid import uuid4

from alembic import command
from alembic.config import Config
import mysql.connector
import pytest

from config.secret_file import RUNTIME_REQUIRED_SECRET_KEYS


SOURCE_HASH = "a" * 64
PLAN_VERSION = "b" * 64
DIGEST = "c" * 64
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def database():
    if os.environ.get("SANDBOX_DATABASE_ACCEPTANCE") != "1":
        pytest.skip("requires explicitly enabled isolated MySQL acceptance")
    host = os.environ.get("MYSQL_WRITE_HOST", "")
    database_name = os.environ.get("MYSQL_DATABASE", "")
    secret_path = Path(os.environ.get("CAUSALAGENT_SECRET_FILE", ""))
    reuse = os.environ.get("SANDBOX_DATABASE_REUSE") == "1"
    if (
        not host.startswith("sandbox-lifecycle-db-")
        or database_name != "sandbox_lifecycle_acceptance"
        or str(secret_path) != "/tmp/sandbox-lifecycle-secrets.env"
    ):
        pytest.fail("refusing an environment outside the isolated acceptance boundary")
    previous = {}
    if secret_path.is_file():
        previous = dict(line.split("=", 1) for line in secret_path.read_text(encoding="utf-8").splitlines() if "=" in line)
    connection = None
    for _ in range(60):
        try:
            connection = mysql.connector.connect(host=host, user="root", password=previous.get("MYSQL_WRITE_PASSWORD", ""), database=database_name, connection_timeout=2)
            break
        except mysql.connector.Error:
            time.sleep(0.5)
    assert connection is not None, "isolated MySQL did not become available"
    with connection.cursor() as cursor:
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        if tables and not reuse:
            pytest.fail("acceptance requires a new empty database; explicit reuse is required for existing acceptance records")
        if reuse:
            assert previous and tables, "explicit reuse requires this suite's existing credentials and schema"
        password = previous.get("MYSQL_WRITE_PASSWORD") if reuse else secrets.token_urlsafe(32)
        if not reuse:
            cursor.execute("ALTER USER CURRENT_USER() IDENTIFIED BY %s", (password,))
    connection.close()
    if not reuse:
        values = {key: secrets.token_urlsafe(32) for key in RUNTIME_REQUIRED_SECRET_KEYS}
        values.update(MYSQL_PASSWORD=password, MYSQL_WRITE_PASSWORD=password, MYSQL_READ_PASSWORD=password)
        secret_path.write_text(
            "".join(f"{key}={value}\n" for key, value in values.items()),
            encoding="utf-8",
        )
        secret_path.chmod(0o600)
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["configure_logger"] = False
    if not reuse:
        command.upgrade(config, "head")
        # Explicit rollback target; only empty newly created tables can be removed.
        command.downgrade(config, "e1f2a3b4c5d6")
    command.upgrade(config, "head")
    store = importlib.import_module("Database.sandbox_sessions")
    db = importlib.import_module("app.db")
    db.check_database_readiness()
    yield store, db, config


def _sql(database, sql: str, args: tuple = (), *, fetch: bool = False):
    _, db, _ = database
    connection = db.get_write_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(sql, args)
        result = cursor.fetchall() if fetch else cursor.lastrowid
        connection.commit()
        cursor.close()
        return result
    finally:
        connection.close()


def _job(database) -> dict:
    user_id = _sql(database, "INSERT INTO users (username, password_hash) VALUES (%s, %s)", ("sandbox-" + uuid4().hex, "acceptance-unused-password"))
    session_id, job_id = str(uuid4()), str(uuid4())
    _sql(database, "INSERT INTO sessions (id, user_id) VALUES (%s, %s)", (session_id, user_id))
    _sql(
        database,
        """INSERT INTO analysis_jobs
           (job_id, user_id, session_id, message, status, worker_id,
            attempt_count, lease_epoch, execution_state, locked_at, heartbeat_at, input_file_hash)
           VALUES (%s, %s, %s, 'isolated lifecycle acceptance', 'running', 'acceptance-worker',
                   1, 1, 'leased', UTC_TIMESTAMP(6), UTC_TIMESTAMP(6), %s)""",
        (job_id, user_id, session_id, SOURCE_HASH),
    )
    identity = {"job_id": job_id, "worker_id": "acceptance-worker", "attempt_count": 1, "lease_epoch": 1,
                "user_id": user_id, "session_id": session_id, "plan_version": PLAN_VERSION}
    _input(database, identity, {"action": "confirm", "plan_version": PLAN_VERSION}, 1)
    return identity


def _input(database, identity: dict, value, sequence: int) -> None:
    _sql(database,
         """INSERT INTO analysis_job_inputs (job_id, sequence, input_type, input_text, idempotency_key, request_fingerprint)
            VALUES (%s, %s, 'resume', %s, %s, %s)""",
         (identity["job_id"], sequence, json.dumps(value), str(uuid4()), DIGEST))


@pytest.fixture
def scope(database):
    store, _, _ = database
    # All remote identifiers in this suite are DB fixtures. No upstream resource
    # is created, so absence is known and closing these test records is valid.
    assert store.count_non_closed_sessions() == 0
    yield database
    for row in store.list_open_sessions(limit=1000):
        store.update_session(row["request_id"], expected_version=row["version"],
                             changes={"status": "closed"}, remote_absent_confirmed=True)


def _reserve(database, identity: dict, *, request_id: str | None = None, capacity: int = 10):
    store, _, _ = database
    request_id = request_id or str(uuid4())
    row, created = store.reserve_session(identity, request_id, DIGEST, SOURCE_HASH, capacity, 300, max_lifetime_seconds=600)
    assert created
    return row


def _active(database, identity: dict) -> dict:
    store, _, _ = database
    row = _reserve(database, identity)
    return store.update_session(row["request_id"], expected_version=row["version"],
                                changes={"status": "active", "remote_sandbox_id": "db-fixture-" + uuid4().hex})


def test_empty_upgrade_explicit_downgrade_and_schema_inspection(database):
    store, _, _ = database
    tables = _sql(database, "SHOW TABLES", fetch=True)
    names = {next(iter(row.values())) for row in tables}
    assert {"analysis_job_datasets", "analysis_sandbox_sessions"} <= names
    assert "analysis_sandbox_capacity" not in names
    assert "analysis_sandbox_leases" not in names
    constraints = _sql(database,
        """SELECT constraint_name FROM information_schema.referential_constraints
           WHERE constraint_schema = DATABASE() AND table_name = 'analysis_sandbox_sessions'""", fetch=True)
    assert constraints == []
    audit = importlib.import_module("Database.deep_audit")
    assert audit._schema_check()["status"] == "healthy"
    inspection = importlib.import_module("Database.inspection")
    for definition in inspection._operational_integrity_definitions(3000):
        if "sandbox" in definition["key"]:
            result = _sql(database, definition["sql"], fetch=True)
            assert result[0]["count_value"] == (0 if definition["healthy_when"] == "zero" else 1)
    assert store.count_non_closed_sessions() == 0


def test_request_replay_and_digest_conflict(scope):
    store, _, _ = scope
    identity = _job(scope)
    row = _reserve(scope, identity)
    repeated, created = store.reserve_session(identity, row["request_id"], DIGEST, SOURCE_HASH, 1, 300)
    assert not created and repeated["id"] == row["id"]
    with pytest.raises(store.SandboxConflictError):
        store.reserve_session(identity, row["request_id"], "d" * 64, SOURCE_HASH, 1, 300)
    with pytest.raises(store.SandboxConflictError):
        _reserve(scope, identity)
    assert store.count_non_closed_sessions() == 1


def test_competing_real_connections_cannot_take_last_slot_twice(scope):
    store, _, _ = scope
    identities = [_job(scope), _job(scope)]
    barrier = threading.Barrier(2)
    def compete(identity):
        barrier.wait(timeout=10)
        try:
            _reserve(scope, identity, capacity=1)
            return "created"
        except store.SandboxCapacityError:
            return "capacity"
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(compete, identities))
    assert sorted(outcomes) == ["capacity", "created"]
    assert store.count_non_closed_sessions() == 1


@pytest.mark.parametrize("status", ["creating", "active", "deleting", "uncertain"])
def test_expired_unclosed_resource_still_occupies_capacity(scope, status):
    store, _, _ = scope
    row = _active(scope, _job(scope)) if status == "active" else _reserve(scope, _job(scope))
    if row["status"] != status:
        row = store.update_session(row["request_id"], expected_version=row["version"], changes={"status": status})
    _sql(scope,
         """UPDATE analysis_sandbox_sessions SET created_at = TIMESTAMPADD(SECOND, -10, UTC_TIMESTAMP(6)),
            expires_at = TIMESTAMPADD(SECOND, -1, UTC_TIMESTAMP(6)) WHERE request_id = %s""",
         (row["request_id"],))
    assert store.get_session(row["request_id"])["expired"]
    with pytest.raises(store.SandboxCapacityError):
        _reserve(scope, _job(scope), capacity=1)
    assert store.count_non_closed_sessions() == 1


def test_execute_pending_replay_heartbeat_cas_and_completed_result(scope):
    store, _, _ = scope
    identity = _job(scope)
    row = _active(scope, identity)
    request_id = row["request_id"]
    store.require_plan_confirmation(request_id, identity)
    begun, started = store.begin_operation(request_id, identity, operation_id="execute-1", operation_type="execute", operation_digest=DIGEST)
    assert started and begun["operation_status"] == "pending"
    replay, started = store.begin_operation(request_id, identity, operation_id="execute-1", operation_type="execute", operation_digest=DIGEST)
    assert not started and replay["operation_status"] == "pending"
    with ThreadPoolExecutor(max_workers=2) as executor:
        heartbeats = list(executor.map(lambda _: store.heartbeat_session(request_id, identity), range(2)))
    assert max(row["version"] for row in heartbeats) == begun["version"] + 2
    result = {"exit_code": 0, "stdout": "bounded result", "stderr": ""}
    with pytest.raises(store.SandboxVersionError):
        store.finish_operation(request_id, operation_id="execute-1", operation_digest=DIGEST,
                               expected_version=begun["version"], operation_status="succeeded", operation_result=result)
    latest = store.get_session(request_id)
    finished = store.finish_operation(request_id, operation_id="execute-1", operation_digest=DIGEST,
                                      expected_version=latest["version"], operation_status="succeeded", operation_result=result)
    heartbeat = store.heartbeat_session(request_id, identity)
    assert heartbeat["operation_result"] == result and heartbeat["expires_at"] == finished["expires_at"]
    replay, started = store.begin_operation(request_id, identity, operation_id="execute-1", operation_type="execute", operation_digest=DIGEST)
    assert not started and replay["operation_result"] == result
    with pytest.raises(store.SandboxConflictError):
        store.begin_operation(request_id, identity, operation_id="execute-2", operation_type="execute", operation_digest=DIGEST)
    with pytest.raises(store.SandboxConflictError):
        store.finish_operation(request_id, operation_id="execute-1", operation_digest="d" * 64,
                               expected_version=heartbeat["version"], operation_status="uncertain", operation_result=None)


def test_unknown_execute_never_authorizes_a_second_execution(scope):
    store, _, _ = scope
    identity = _job(scope)
    row = _active(scope, identity)
    row, _ = store.begin_operation(row["request_id"], identity, operation_id="once", operation_type="execute", operation_digest=DIGEST)
    row = store.finish_operation(row["request_id"], operation_id="once", operation_digest=DIGEST,
                                 expected_version=row["version"], operation_status="uncertain", operation_result=None,
                                 changes={"status": "uncertain"})
    replay, started = store.begin_operation(row["request_id"], identity, operation_id="once", operation_type="execute", operation_digest=DIGEST)
    assert not started and replay["operation_status"] == "uncertain"
    assert store.count_non_closed_sessions() == 1


@pytest.mark.parametrize("change", [
    "status = 'canceled', execution_state = 'draining'",
    "status = 'queued', execution_state = NULL, worker_id = NULL, locked_at = NULL",
    "worker_id = 'other-worker'",
    "attempt_count = 2", "lease_epoch = 2", "heartbeat_at = TIMESTAMPADD(SECOND, -121, UTC_TIMESTAMP(6))",
    "input_file_hash = REPEAT('f', 64)",
])
def test_real_current_job_fencing_blocks_old_owner(scope, change):
    store, _, _ = scope
    identity = _job(scope)
    row = _active(scope, identity)
    _sql(scope, "UPDATE analysis_jobs SET " + change + " WHERE job_id = %s", (identity["job_id"],))
    with pytest.raises(store.SandboxFencedError):
        store.require_current_owner(identity, SOURCE_HASH)
    with pytest.raises(store.SandboxFencedError):
        store.require_owner(row["request_id"], identity)
    with pytest.raises(store.SandboxFencedError):
        store.heartbeat_session(row["request_id"], identity)
    with pytest.raises(store.SandboxFencedError):
        store.begin_operation(row["request_id"], identity, operation_id="denied", operation_type="execute", operation_digest=DIGEST)
    candidates = store.list_reconciliation_candidates(heartbeat_timeout_seconds=300)
    assert row["request_id"] in {row["request_id"] for row in candidates}


def test_latest_free_text_invalidates_prior_confirmation(scope):
    store, _, _ = scope
    identity = _job(scope)
    row = _active(scope, identity)
    _input(scope, identity, "change the processing scope", 2)
    with pytest.raises(store.SandboxFencedError):
        store.require_plan_confirmation(row["request_id"], identity)
    with pytest.raises(store.SandboxFencedError):
        store.begin_operation(row["request_id"], identity, operation_id="denied", operation_type="execute", operation_digest=DIGEST)
    assert store.get_session(row["request_id"])["operation_id"] is None


def test_execute_cannot_be_replayed_under_a_new_confirmed_plan(scope):
    store, _, _ = scope
    identity = _job(scope)
    row = _active(scope, identity)
    row, _ = store.begin_operation(row["request_id"], identity, operation_id="plan-bound", operation_type="execute", operation_digest=DIGEST)
    assert row["metadata"]["execute_plan_version"] == PLAN_VERSION
    new_plan = "e" * 64
    _input(scope, identity, {"action": "confirm", "plan_version": new_plan}, 2)
    with pytest.raises(store.SandboxConflictError):
        store.begin_operation(row["request_id"], {**identity, "plan_version": new_plan},
                              operation_id="plan-bound", operation_type="execute", operation_digest=DIGEST)
    with pytest.raises(store.SandboxConflictError):
        store.update_session(row["request_id"], expected_version=row["version"], changes={"metadata": {}})


def test_closed_record_cannot_accept_late_execute_completion(scope):
    store, _, _ = scope
    identity = _job(scope)
    row = _active(scope, identity)
    row, _ = store.begin_operation(row["request_id"], identity, operation_id="late", operation_type="execute", operation_digest=DIGEST)
    row = store.update_session(row["request_id"], expected_version=row["version"], changes={"status": "closed"}, remote_absent_confirmed=True)
    with pytest.raises(store.SandboxConflictError):
        store.finish_operation(row["request_id"], operation_id="late", operation_digest=DIGEST,
                               expected_version=row["version"], operation_status="succeeded", operation_result={"exit_code": 0})
    assert store.get_session(row["request_id"])["version"] == row["version"]


@pytest.mark.parametrize("entity", ["analysis_jobs", "sessions", "users"])
def test_business_deletion_preserves_resource_and_cleanup(scope, entity):
    store, _, _ = scope
    identity = _job(scope)
    row = _active(scope, identity)
    column, value = {"analysis_jobs": ("job_id", identity["job_id"]), "sessions": ("id", identity["session_id"]), "users": ("id", identity["user_id"])}[entity]
    _sql(scope, f"DELETE FROM {entity} WHERE {column} = %s", (value,))
    with pytest.raises(store.SandboxFencedError):
        store.require_owner(row["request_id"], identity)
    assert store.get_session(row["request_id"])["remote_sandbox_id"] == row["remote_sandbox_id"]
    assert store.count_non_closed_sessions() == 1
    row = store.update_session(row["request_id"], expected_version=row["version"], changes={"status": "deleting"})
    row = store.update_session(row["request_id"], expected_version=row["version"], changes={"status": "closed"}, remote_absent_confirmed=True)
    assert row["closed_at"] is not None and store.count_non_closed_sessions() == 0


def test_close_requires_absence_and_renewal_is_bounded(scope):
    store, _, _ = scope
    row = _reserve(scope, _job(scope))
    with pytest.raises(store.SandboxConflictError):
        store.update_session(row["request_id"], expected_version=row["version"], changes={"status": "closed"})
    with pytest.raises(store.SandboxConflictError):
        store.update_session(row["request_id"], expected_version=row["version"], changes={"expires_at": row["max_expires_at"] + timedelta(seconds=1)})
    with pytest.raises(store.SandboxVersionError):
        store.update_session(row["request_id"], expected_version=row["version"] + 1, changes={"status": "uncertain"})
    row = store.update_session(row["request_id"], expected_version=row["version"], changes={"status": "uncertain"})
    assert row["remote_sandbox_id"] is None and store.count_non_closed_sessions() == 1


def test_open_listing_keyset_and_due_retry(scope):
    store, _, _ = scope
    rows = [_reserve(scope, _job(scope)) for _ in range(3)]
    first = store.list_open_sessions(limit=2)
    second = store.list_open_sessions(after_id=first[-1]["id"], limit=2)
    assert [row["id"] for row in first + second] == [row["id"] for row in rows]
    row = rows[0]
    store.update_session(row["request_id"], expected_version=row["version"], changes={"next_retry_at": row["max_expires_at"]})
    due = store.list_reconciliation_candidates(heartbeat_timeout_seconds=300)
    assert {row["request_id"] for row in due} == {row["request_id"] for row in rows[1:]}


def test_downgrade_refuses_retained_closed_lifecycle_records(database):
    store, _, config = database
    assert store.count_non_closed_sessions() == 0
    with pytest.raises(RuntimeError, match="archive lifecycle records"):
        command.downgrade(config, "e1f2a3b4c5d6")
    assert _sql(database, "SELECT version_num FROM alembic_version", fetch=True)[0]["version_num"] == "x1y2z3a4b5c6"
