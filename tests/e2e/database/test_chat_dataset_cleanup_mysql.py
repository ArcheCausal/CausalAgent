"""真实 MySQL、Flask 会话和删除事务验证；仅用于显式隔离数据库验收。"""

from secrets import token_hex

import pytest

from tests.e2e.database.test_sandbox_sessions_mysql import database, _sql


@pytest.mark.parametrize("keep_data_reference", [False, True])
def test_delete_session_enqueues_cleanup_and_reclaims_only_unreferenced_datasets(database, keep_data_reference):
    from flask import Flask

    from app.chat.routes import chat_bp
    from tests.support.mysql_bound_job import create_bound_job

    identity = create_bound_job(database)
    rows = _sql(database, "SELECT data_object_id, script_object_id FROM analysis_job_datasets WHERE job_id = %s",
                (identity.job_id,), fetch=True)
    data_id, script_id = rows[0]["data_object_id"], rows[0]["script_object_id"]
    if keep_data_reference:
        _sql(database, "INSERT INTO user_files (user_id, object_id, filename, mime_type, file_size) "
             "SELECT %s, id, 'retained.csv', mime_type, file_size FROM file_objects WHERE id = %s",
             (identity.user_id, data_id))
    _sql(database, "UPDATE analysis_jobs SET status = 'succeeded', execution_state = NULL, worker_id = NULL, "
         "locked_at = NULL, execution_released_at = UTC_TIMESTAMP(6), execution_release_reason = 'worker_confirmed' WHERE job_id = %s",
         (identity.job_id,))
    app = Flask(__name__)
    app.secret_key = token_hex(32)
    app.register_blueprint(chat_bp)
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = identity.user_id
            session["auth_version"] = 1
        response = client.post("/api/delete_session", json={"session_id": identity.session_id})
    assert response.status_code == 202, response.get_json()
    for table, column, value in (
        ("sessions", "id", identity.session_id),
        ("analysis_jobs", "job_id", identity.job_id),
        ("analysis_job_datasets", "job_id", identity.job_id),
    ):
        assert not _sql(database, f"SELECT 1 FROM {table} WHERE {column} = %s", (value,), fetch=True)
    outbox = _sql(database, "SELECT status FROM checkpoint_cleanup_outbox WHERE thread_id = %s", (identity.job_id,), fetch=True)
    assert len(outbox) == 1 and outbox[0]["status"] == "pending"
    assert not _sql(database, "SELECT 1 FROM file_objects WHERE id = %s", (script_id,), fetch=True)
    retained = _sql(database, "SELECT 1 FROM file_objects WHERE id = %s", (data_id,), fetch=True)
    assert bool(retained) is keep_data_reference
    assert not _sql(database, "SELECT 1 FROM user_memory_cleanup_outbox WHERE user_id = %s", (identity.user_id,), fetch=True)
