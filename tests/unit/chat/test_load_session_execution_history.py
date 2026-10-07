import json
import os
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
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

from app.chat.routes import chat_bp  # noqa: E402


BASE = datetime(2026, 8, 11, 8, 0, 0)


class HistoryCursor:
    def __init__(self):
        self.current_sql = ""
        self.statements = []

    def execute(self, sql, params=None):
        self.current_sql = " ".join(sql.split())
        self.statements.append((self.current_sql, params))

    def fetchone(self):
        if "FROM sessions" in self.current_sql:
            return {
                "id": "session-1",
                "user_id": 7,
                "snapshot_at": BASE + timedelta(seconds=5),
            }
        return None

    def fetchall(self):
        if "FROM chat_messages" in self.current_sql:
            return [
                {
                    "id": 10,
                    "message_type": "user",
                    "content": "问题",
                    "has_attachment": False,
                    "analysis_job_id": "job-1",
                    "analysis_job_input_id": 1,
                    "source_event_id": None,
                    "created_at": BASE,
                },
                {
                    "id": 11,
                    "message_type": "ai",
                    "content": "回答",
                    "has_attachment": False,
                    "analysis_job_id": "job-1",
                    "analysis_job_input_id": 1,
                    "source_event_id": 102,
                    "created_at": BASE + timedelta(seconds=3),
                },
            ]
        if "FROM analysis_jobs" in self.current_sql:
            return [{"job_id": "job-1", "status": "succeeded", "created_at": BASE}]
        if "FROM analysis_job_inputs" in self.current_sql:
            return [{
                "input_id": 1,
                "job_id": "job-1",
                "sequence": 0,
                "input_type": "initial",
                "chat_message_id": 10,
                "created_at": BASE,
            }]
        if "FROM analysis_job_events" in self.current_sql:
            return [
                {
                    "id": 101,
                    "job_id": "job-1",
                    "event_type": "node_start",
                    "payload_json": json.dumps({
                        "type": "node_start",
                        "step_id": "step-1",
                        "title": "分析",
                        "attempt": 7,
                    }),
                    "created_at": BASE + timedelta(seconds=1),
                },
                {
                    "id": 102,
                    "job_id": "job-1",
                    "event_type": "final_result",
                    "payload_json": json.dumps({"type": "final_result", "data": {"secret": "not returned"}}),
                    "created_at": BASE + timedelta(seconds=3),
                },
            ]
        return []


class HistoryConnection:
    def __init__(self):
        self.cursor_value = HistoryCursor()
        self.transaction_options = None
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def start_transaction(self, **kwargs):
        self.transaction_options = kwargs

    def cursor(self, **_kwargs):
        return self.cursor_value

    def commit(self):
        self.committed = True

    def rollback(self):
        pass


class DeniedHistoryCursor(HistoryCursor):
    def fetchone(self):
        return None


class DeniedHistoryConnection(HistoryConnection):
    def __init__(self):
        super().__init__()
        self.cursor_value = DeniedHistoryCursor()
        self.rolled_back = False

    def rollback(self):
        self.rolled_back = True


# 早期版本把 Agent 内部图格式写进了 causal_graph 附件。
STORED_INTERNAL_GRAPH = {
    "type": "causal_graph",
    "layout": "report",
    "summary": "报告正文",
    "graph_source": "original",
    "data": {
        "graph_semantics": "dag_target_to_source",
        "nodes": ["A", "B"],
        "edges": [
            {"source": "A", "target": "B", "edge_type": "directed", "weight": 2.0}
        ],
    },
}


class GraphAttachmentHistoryCursor(HistoryCursor):
    def fetchall(self):
        if "FROM chat_messages" in self.current_sql:
            rows = super().fetchall()
            for row in rows:
                if row["id"] == 11:
                    row["has_attachment"] = True
            return rows
        if "FROM chat_attachments" in self.current_sql:
            return [
                {
                    "message_id": 11,
                    "attachment_type": "causal_graph",
                    "content": json.dumps(STORED_INTERNAL_GRAPH, ensure_ascii=False),
                }
            ]
        return super().fetchall()


class GraphAttachmentHistoryConnection(HistoryConnection):
    def __init__(self):
        super().__init__()
        self.cursor_value = GraphAttachmentHistoryCursor()


STORED_REPORT_DOCUMENT = {
    "schema_version": 1,
    "report_id": "report_hist",
    "title": "因果分析报告",
    "blocks": [
        {"id": "markdown_summary", "type": "markdown", "content": "结论正文", "evidence_refs": []}
    ],
    "assets": {
        "chart_histogram_age": {
            "asset_key": "chart_histogram_age",
            "type": "chart",
            "chart_type": "histogram",
            "data": {"bins": [0, 1, 2], "counts": [3, 4]},
            "metadata": {"variable": "age"},
            "options": {"show_tooltip": True},
        }
    },
    "sources": [{"source_id": "src_1", "kind": "file", "title": "data.csv", "file_id": 12}],
    "evidence_refs": [],
}


class ReportAttachmentHistoryCursor(HistoryCursor):
    def __init__(self, content: str | None = None):
        super().__init__()
        self.content = (
            json.dumps(STORED_REPORT_DOCUMENT, ensure_ascii=False)
            if content is None
            else content
        )

    def fetchall(self):
        if "FROM chat_messages" in self.current_sql:
            rows = super().fetchall()
            for row in rows:
                if row["id"] == 11:
                    row["has_attachment"] = True
            return rows
        if "FROM chat_attachments" in self.current_sql:
            return [
                {
                    "message_id": 11,
                    "attachment_type": "report_document",
                    "content": self.content,
                }
            ]
        return super().fetchall()


class ReportAttachmentHistoryConnection(HistoryConnection):
    def __init__(self, content: str | None = None):
        super().__init__()
        self.cursor_value = ReportAttachmentHistoryCursor(content)


def _app():
    app = Flask(__name__)
    app.secret_key = "history-test"
    app.register_blueprint(chat_bp)
    return app


def test_load_session_returns_phase_and_uses_fixed_batch_queries():
    connection = HistoryConnection()
    with (
        patch("app.chat.routes.get_current_session_user", return_value={"id": 7, "username": "owner"}),
        patch("app.db.get_read_connection", return_value=connection),
        _app().test_client() as client,
    ):
        response = client.get("/api/load_session?session=session-1")

    assert response.status_code == 200
    messages = response.get_json()["messages"]
    assert [message["sender"] for message in messages] == ["user", "ai"]
    assert messages[0]["text"] == "问题"
    assert messages[0]["analysis_job_id"] == "job-1"
    assert messages[0]["analysis_job_input_id"] == 1
    assert messages[1]["text"] == "回答"
    assert messages[1]["analysis_job_id"] == "job-1"
    assert messages[1]["analysis_job_input_id"] == 1
    phase = messages[0]["thinking_after"]
    assert set(phase) == {
        "phase_sequence",
        "status",
        "elapsed_seconds",
        "last_event_id",
        "events",
        "analysis_job_id",
        "analysis_job_input_id",
    }
    assert phase["phase_sequence"] == 0
    assert phase["status"] == "completed"
    assert phase["analysis_job_id"] == "job-1"
    assert phase["analysis_job_input_id"] == 1
    assert [event["event_id"] for event in phase["events"]] == [101]
    assert "attempt" not in phase["events"][0]
    assert "secret" not in repr(phase)
    assert connection.transaction_options == {"readonly": True}
    assert connection.committed is True
    assert len(connection.cursor_value.statements) == 5
    assert sum("FROM analysis_job_events" in sql for sql, _ in connection.cursor_value.statements) == 1
    event_sql = next(sql for sql, _ in connection.cursor_value.statements if "FROM analysis_job_events" in sql)
    assert "FORCE INDEX (idx_analysis_job_events_job_id)" in event_sql
    assert "ORDER BY job_id, id" in event_sql
    assert phase["last_event_id"] == 102


def test_load_session_rejects_unknown_or_unauthorized_session_before_history_queries():
    connection = DeniedHistoryConnection()
    with (
        patch("app.chat.routes.get_current_session_user", return_value={"id": 7, "username": "owner"}),
        patch("app.db.get_read_connection", return_value=connection),
        _app().test_client() as client,
    ):
        response = client.get("/api/load_session?session=not-owned")

    assert response.status_code == 404
    assert connection.rolled_back is True
    assert len(connection.cursor_value.statements) == 2


def test_load_session_projects_stored_graph_for_frontend():
    """历史附件里的内部图格式必须在读取时投影成前端可渲染的载荷。"""
    connection = GraphAttachmentHistoryConnection()
    with (
        patch("app.chat.routes.get_current_session_user", return_value={"id": 7, "username": "owner"}),
        patch("app.db.get_read_connection", return_value=connection),
        _app().test_client() as client,
    ):
        response = client.get("/api/load_session?session=session-1")

    assert response.status_code == 200
    ai_message = response.get_json()["messages"][1]
    assert ai_message["text"]["summary"] == "报告正文"
    graph = ai_message["text"]["data"]
    assert graph["nodes"] == [{"id": "A", "label": "A"}, {"id": "B", "label": "B"}]
    assert graph["edges"] == [
        {
            "from": "A",
            "to": "B",
            "arrows": "to",
            "dashes": False,
            "weight": 2.0,
            "label": "2",
        }
    ]


def test_load_session_restores_structured_report_document():
    """历史接口把 report_document 附件恢复成前端直接可渲染的结构化报告。"""
    connection = ReportAttachmentHistoryConnection()
    with (
        patch("app.chat.routes.get_current_session_user", return_value={"id": 7, "username": "owner"}),
        patch("app.db.get_read_connection", return_value=connection),
        _app().test_client() as client,
    ):
        response = client.get("/api/load_session?session=session-1")

    assert response.status_code == 200
    ai_message = response.get_json()["messages"][1]
    report = ai_message["text"]
    assert report["type"] == "report"
    assert report["layout"] == "report"
    assert report["render_mode"] == "structured"
    assert report["document"]["report_id"] == "report_hist"
    assert report["document"]["blocks"][0]["content"] == "结论正文"
    assert report["document"]["assets"]["chart_histogram_age"]["chart_type"] == "histogram"
    assert report["document"]["sources"][0]["source_id"] == "src_1"


@pytest.mark.parametrize(
    "content",
    [
        "{不是合法 JSON",
        json.dumps({"report_id": 5, "title": "缺少块", "blocks": []}),
    ],
)
def test_load_session_degrades_broken_report_attachment(content):
    """损坏或 schema 非法的报告附件不能直接进入前端，只回退到消息预览正文。"""
    connection = ReportAttachmentHistoryConnection(content)
    with (
        patch("app.chat.routes.get_current_session_user", return_value={"id": 7, "username": "owner"}),
        patch("app.db.get_read_connection", return_value=connection),
        patch("app.chat.routes.log_event") as log_event,
        _app().test_client() as client,
    ):
        response = client.get("/api/load_session?session=session-1")

    assert response.status_code == 200
    ai_message = response.get_json()["messages"][1]
    assert ai_message["text"] == "回答"
    degraded_calls = [
        call for call in log_event.call_args_list
        if call.args and call.args[1] == "chat.attachment.degraded"
    ]
    assert degraded_calls
    assert degraded_calls[0].kwargs["details"]["attachment_type"] == "report_document"
    assert degraded_calls[0].kwargs["details"]["reason_code"] == "protocol_error"
