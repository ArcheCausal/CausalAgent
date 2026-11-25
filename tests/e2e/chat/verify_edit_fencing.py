"""Check real MySQL edit transactions, active/draining conflicts and late worker writes."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pandas as pd

from Agent.Processing.fold_processing import get_data_summary
from Agent.Report.assets import build_chart_assets
from app import create_app
from app.agent import job_service as jobs
from app.db import get_read_connection, get_write_connection
from config.database_settings import database_settings
from Database.analysis_contexts import AnalysisContextFencedError, preprocess_version


def test_edit(app, username, password):
    client = app.test_client()
    assert client.post("/api/register", json={"username": username, "password": password}).status_code == 200
    auth = client.post("/api/login", json={"username": username, "password": password}).get_json()
    token = auth["csrf_token"]
    session_id = client.post("/api/new_chat").get_json()["new_session_id"]
    dataset = Path(__file__).resolve().parents[2] / "dataset" / "sachs.csv"
    with dataset.open("rb") as file:
        uploaded = client.post("/api/upload_file", data={"file": (file, dataset.name)})
    assert uploaded.status_code in {200, 201}
    file_id = uploaded.get_json()["file"]["user_file_id"]
    request_headers = {"Idempotency-Key": str(uuid4())}
    created = client.post(
        "/api/agent/jobs",
        json={"session_id": session_id, "message": "要替换的输入", "input_user_file_id": file_id},
        headers=request_headers,
    )
    assert created.status_code == 202
    initial = created.get_json()
    title = "用户自定义标题"
    assert client.post("/api/change_session", json={"session_id": session_id, "title": title}).status_code == 200
    worker = jobs.claim_next_job("edit-acceptance-worker")
    assert worker and worker["job_id"] == initial["job_id"]
    frame = pd.read_csv(dataset)
    parameters = get_data_summary(frame)
    charts = {key: chart.model_dump(mode="json") for key, chart in build_chart_assets(frame, parameters).items()}
    assert charts
    output = {"chart_assets": charts}
    version_options = {
        "job_id": worker["job_id"], "worker_id": worker["worker_id"],
        "attempt_count": worker["attempt_count"], "lease_epoch": worker["lease_epoch"],
        "call_id": str(uuid4()), "source_file_hash": worker["input_file_hash"],
        "recipe": {"pipeline": "csv_profile_charts_summary_v1", "target": None, "treatment": None},
    }
    version = preprocess_version(output=output, **version_options)
    assert version and preprocess_version(output=output, **version_options) == version
    user_id = worker["user_id"]
    edit_url = f"/api/sessions/{session_id}/messages/{initial['message_id']}/edit-resend"
    payload = {"message": "编辑后的输入", "conversation_version": initial["conversation_version"]}
    edit_key = str(uuid4())

    def submit(test_client=client, *, key=edit_key, body=payload, csrf=token):
        return test_client.post(edit_url, json=body, headers={"Idempotency-Key": key, "X-CSRF-Token": csrf})

    assert submit(csrf="").status_code == 403
    assert submit().get_json()["code"] == "active_job_conflict"
    response = client.post(f"/api/agent/jobs/{worker['job_id']}/cancel", headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == 202
    history = client.get(f"/api/load_session?session={session_id}").get_json()
    assert len(history["messages"]) == 2
    assert submit().get_json()["code"] == "conversation_version_conflict"
    payload["conversation_version"] = history["conversation_version"]
    assert submit().get_json()["code"] == "job_draining"
    assert jobs.release_execution_ownership(
        worker["job_id"], worker["worker_id"], worker["attempt_count"], worker["lease_epoch"],
    )

    spoofed = submit(body={**payload, "checkpoint_id": str(uuid4())})
    assert spoofed.status_code == 409 and spoofed.get_json()["code"] == "edit_checkpoint_conflict"
    assert client.get(f"/api/load_session?session={session_id}").get_json() == history

    # Force MySQL to reject the replacement ledger write after the suffix was marked.
    trigger = f"reject_edit_{uuid4().hex}"
    with get_write_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(
            f"CREATE TRIGGER {trigger} BEFORE INSERT ON analysis_job_inputs FOR EACH ROW "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'edit acceptance rollback'"
        )
    try:
        assert submit().status_code == 500
        assert client.get(f"/api/load_session?session={session_id}").get_json() == history
    finally:
        with get_write_connection() as connection:
            connection.cursor().execute(f"DROP TRIGGER {trigger}")

    gate = Barrier(2)

    def replay_concurrently(key):
        with app.test_client() as test_client:
            logged_in = test_client.post("/api/login", json={"username": username, "password": password}).get_json()
            gate.wait(timeout=10)
            response = submit(test_client, csrf=logged_in["csrf_token"], key=key)
            return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        a, b = executor.map(replay_concurrently, [edit_key, edit_key])
    assert sorted([a[0], b[0]]) == [200, 202]
    assert a[1]["job_id"] == b[1]["job_id"]
    assert submit(body={**payload, "message": "不同请求"}).get_json()["code"] == "idempotency_conflict"

    assert jobs.write_event(
        worker["job_id"], worker["worker_id"], worker["attempt_count"],
        "progress", {"type": "progress", "message": "迟到"}, lease_epoch=worker["lease_epoch"],
    ) is None
    assert not jobs.complete_job_with_chat(
        worker, worker["worker_id"], "final_result", {"type": "final_result"},
        {"type": "text", "summary": "迟到"}, {}, lease_epoch=worker["lease_epoch"],
    )
    try:
        preprocess_version(output=output, **version_options)
    except AnalysisContextFencedError:
        pass
    else:
        raise AssertionError("The old lease was able to submit a preprocessing version")
    foreign = app.test_client()
    other_user = f"foreign_{uuid4().hex[:12]}"
    assert foreign.post("/api/register", json={"username": other_user, "password": password}).status_code == 200
    foreign_auth = foreign.post("/api/login", json={"username": other_user, "password": password}).get_json()
    assert submit(foreign, csrf=foreign_auth["csrf_token"], key=str(uuid4())).status_code == 404
    assert client.get(f"/api/agent/jobs/{worker['job_id']}/events").status_code == 404

    final = client.get(f"/api/load_session?session={session_id}").get_json()
    assert [message["message_id"] for message in final["messages"]] == [a[1]["message_id"]]
    assert final["conversation_version"] == history["conversation_version"] + 1
    sessions = dict(client.get("/api/sessions").get_json())
    assert sessions[session_id]["preview"] == title
    with get_read_connection(consistency="strong") as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT COUNT(*) AS total, SUM(replaced_by_job_id IS NOT NULL) AS replaced
            FROM chat_messages WHERE session_id = %s AND user_id = %s
            """,
            (session_id, user_id),
        )
        assert cursor.fetchone() == {"total": 3, "replaced": 2}
    jobs.cancel_job(user_id, a[1]["job_id"], str(uuid4()))


if __name__ == "__main__":
    if not database_settings.MYSQL_DATABASE.startswith("causalagent_edit"):
        raise SystemExit("This acceptance test requires a disposable causalagent_edit* database.")
    test_edit(create_app(), f"fence_{uuid4().hex[:12]}", f"A3{uuid4()}")
    print("MySQL and Flask: rollback, permissions, concurrency, draining, preprocessing retry and late worker fencing passed.")
