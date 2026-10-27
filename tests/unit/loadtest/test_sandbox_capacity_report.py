"""沙盒容量与完整排空结论的纯函数检查；只使用固定摘要，不读取真实运行。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "loadtest"))

from runner.capacity import enrich_summary  # noqa: E402
from runner.report import render_stage_report  # noqa: E402

RUN_ID = "20261026T000000-sandbox_baseline"


def session(**overrides):
    row = {"job_id": "job-1", "request_id": "a" * 64, "remote_sandbox_id": "remote-1",
           "status": "closed", "operation_status": "succeeded",
           "created_at": "2026-10-26T00:00:00Z", "closed_at": "2026-10-26T00:00:30Z"}
    row.update(overrides)
    return row


def summary(sandbox, jobs=None):
    jobs = jobs if jobs is not None else [{"job_id": "job-1", "status": "succeeded", "execution_state": None,
                                           "created_at": "2026-10-26T00:00:00Z", "finished_at": "2026-10-26T00:00:40Z",
                                           "execution_released_at": "2026-10-26T00:00:41Z"}]
    return {"run_id": RUN_ID, "stage": "sandbox_baseline", "users": 1,
            "schema_version": "loadtest.run.v2", "started_at": 1790000000.0,
            "stop_submitting_at": 1790000100.0, "observation_ended_at": 1790000200.0,
            "requests": [{"logical_request_id": "r1", "job_id": "job-1", "admission": "accepted",
                          "created_status": 202, "terminal_received_at": 1790000050.0,
                          "group": "resumed",
                          "interactions": [{"interaction_type": "data_processing_question", "interaction_status": "answered"},
                                           {"interaction_type": "data_processing_plan_confirmation", "interaction_status": "answered"}]}],
            "job": {"created": 1, "created_ids": ["job-1"],
                    "samples": [{"job_id": "job-1", "db_status": "succeeded", "db_checked": True,
                                 "terminal_received_at": 1790000050.0, "final_status": "succeeded"}]},
            "job_db_check": {"run_id": RUN_ID, "scope": "run", "status": "collected",
                             "jobs": jobs, "sandbox": sandbox}}


def test_closed_sessions_complete_the_drain():
    sandbox = {"status": "collected", "sessions": [session()], "sandbox_drained": True,
               "complete_drained": True, "timings": {"initialized": {"samples": 1, "min_ms": None}}}
    enriched = enrich_summary(summary(sandbox))
    drain = enriched["drain"]
    assert drain["complete_drained"] is True
    assert drain["sandbox_released"] is True
    assert drain["sandbox_sessions_closed"] == 1
    assert enriched["capacity"]["sandbox"]["sessions_per_job"] == 1.0
    assert enriched["capacity"]["interaction"]["plan_confirmations"] == 1


def test_open_session_blocks_complete_drain():
    sandbox = {"status": "collected", "sessions": [session(status="deleting", closed_at=None)],
               "sandbox_drained": False, "complete_drained": False}
    enriched = enrich_summary(summary(sandbox))
    assert enriched["drain"]["complete_drained"] is False
    assert enriched["drain"]["finally_drained"] is False
    assert enriched["drain"]["sandbox_sessions_open"] == 1


def test_missing_session_evidence_stays_unknown():
    enriched = enrich_summary(summary({"status": "missing", "reason": "query_failed"}))
    drain = enriched["drain"]
    assert drain["complete_drained"] is None
    # 采集尝试失败记 missing，旧产物没有该节记 not_collected；两者都不是通过。
    assert drain["sandbox_evidence_status"] == "missing"
    assert drain["sandbox_released"] is None
    assert enriched["capacity"]["sandbox"]["sessions"] is None


def test_legacy_summary_reports_uncollected_sections():
    legacy = {"run_id": RUN_ID, "stage": "low", "users": 2, "schema_version": "loadtest.run.v1",
              "started_at": 1790000000.0}
    body = render_stage_report(legacy)
    assert "交互答复" in body and "沙盒会话与阶段时间" in body
    assert "完整排空 complete_drained" in body
    assert "未采集" in body


def test_multiple_sessions_per_job_are_not_collapsed():
    sandbox = {"status": "collected",
               "sessions": [session(request_id="a" * 64), session(request_id="b" * 64, remote_sandbox_id="remote-2")],
               "sandbox_drained": True, "complete_drained": True}
    enriched = enrich_summary(summary(sandbox))
    sandbox_capacity = enriched["capacity"]["sandbox"]
    assert sandbox_capacity["sessions"] == 2
    assert sandbox_capacity["jobs_with_sessions"] == 1
    assert sandbox_capacity["sessions_per_job"] == 2.0
