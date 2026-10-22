"""只读取现存真实产物，验证资源汇总、清单导出和主库附加兼容性。

不访问服务器，不启动负载，不构造业务 summary。新产物写入 local/evidence/
独立验证目录；原始文件只校验哈希，不修改。
"""
from __future__ import annotations

import hashlib
import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from ops.server_evidence import manifest, request_samples, diagnostic_fields
from ops.stage_sampling import cleanup_result, scenario_result
from runner import config
from runner import resource
from runner.evidence import (_job_ids, _summary_table,
                             _load_logchecks, _render_unobserved_jobs,
                             export_request_manifest, merge_job_db_check)
from runner.report import render_stage_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-local", type=Path, default=config.LOCAL_DIR)
    parser.add_argument("--output-root", type=Path, default=BASE / "local" / "evidence")
    args = parser.parse_args()
    local = args.source_local.resolve()
    destination = args.output_root.resolve() / ("server-evidence-validation-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    destination.mkdir(parents=True, exist_ok=False)
    summaries = sorted((local / "runs").glob("*/summary.json"))
    client_summaries = sorted((local / "runs").glob("*/client-summary.json"))
    samples = sorted(set((local / "runs").glob("*sample*.json")) | set((local / "runs").glob("*/*sample*.json")))
    databases = sorted(set(local.glob("job-db-*.json")) | set((local / "evidence").glob("job-db-*.json"))
                       | set((local / "runs").glob("*/job-db-final.json")))
    logchecks = sorted((local / "evidence").glob("logcheck-*.json"))
    inputs = summaries + client_summaries + samples + databases + logchecks
    hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
    db_by_run = {}
    for path in databases:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if payload.get("run_id"):
            db_by_run[payload["run_id"]] = payload
    totals = {"real_summaries": 0, "real_resource_files": 0, "real_resource_samples": 0,
              "manifest_exports": 0, "matching_db_merges": 0, "cross_run_rejections": 0,
              "unscoped_db_rejections": 0, "legacy_v1_db_merges": 0, "legacy_v1_job_rows": 0,
              "failed_read_samples": 0, "failed_reads_missing_ids": 0,
              "historical_cleanup_confirmed": 0, "historical_cleanup_unknown": 0,
              "historical_recovery_incomplete": 0, "safe_log_diagnostics": 0}
    if not summaries:
        raise ValueError("no_real_summaries")
    for path in summaries:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not payload.get("run_id") or "http" not in payload:
            continue
        totals["real_summaries"] += 1
        reads = request_samples(payload)
        totals["failed_read_samples"] += len(reads)
        totals["failed_reads_missing_ids"] += sum(not any(row.get(key) for key in (
            "request_id", "client_request_id", "response_request_id")) for row in reads)
        original = deepcopy(payload)
        exported = export_request_manifest(payload, destination / f"requests-{path.parent.name}.json")
        listing = manifest(str(exported), payload["run_id"])
        assert listing["run_id"] == payload["run_id"]
        assert listing["job_ids"] == _job_ids(payload)
        assert len(listing["requests"]) == len(payload.get("requests", []))
        assert payload == original
        totals["manifest_exports"] += 1
        db = db_by_run.get(payload["run_id"])
        if db is not None:
            cleanup = cleanup_result(payload, db, payload["run_id"])
            totals["historical_cleanup_confirmed" if cleanup["status"] == "passed" else "historical_cleanup_unknown"] += 1
        if payload.get("stage") == "recovery":
            result = scenario_result("recovery", payload, (payload.get("orchestration") or {}).get("load_exit_code"))
            totals["historical_recovery_incomplete"] += result["status"] == "incomplete"
        if (db is not None and db.get("scope") != "run"
                and db.get("schema_version") != "loadtest-job-db-check-v1"):
            try:
                merge_job_db_check(deepcopy(payload), db)
            except ValueError:
                totals["unscoped_db_rejections"] += 1
            else:
                raise AssertionError("unscoped_db_evidence_accepted")
        elif db is not None:
            merged = merge_job_db_check(deepcopy(payload), db)
            assert merged["job_db_check"] == db
            assert "capacity" in merged
            if not any(isinstance(row.get("persisted_event_ids"), list) for row in db.get("jobs", [])):
                assert merged["capacity"]["sse_integrity"]["missing_events"] is None
            totals["matching_db_merges"] += 1
            if db.get("schema_version") == "loadtest-job-db-check-v1" and db.get("scope") is None:
                assert {str(row["job_id"]) for row in db["jobs"]} <= set(_job_ids(payload))
                totals["legacy_v1_db_merges"] += 1
                totals["legacy_v1_job_rows"] += len(db["jobs"])
        different = next((row for rid, row in db_by_run.items() if rid != payload["run_id"]), None)
        if different is not None:
            try:
                merge_job_db_check(deepcopy(payload), different)
            except ValueError:
                totals["cross_run_rejections"] += 1
            else:
                raise AssertionError("cross_run_evidence_accepted")
    for path in samples:
        payload = resource.load_sample(path)
        assert payload is not None
        original = deepcopy(payload)
        summary = resource.summarize(payload)
        rendered = "\n".join(resource.render(summary))
        assert payload == original
        assert summary["sample_count"] == len(payload.get("samples") or [])
        assert rendered
        if not payload.get("stop_submitting_at") and not payload.get("submission_stopped_at"):
            assert summary["phases"]["load"] is None
            assert summary["phases"]["drain"] is None
        totals["real_resource_files"] += 1
        totals["real_resource_samples"] += summary["sample_count"]
    for path in logchecks:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        for job in (payload.get("job_lifecycle") or {}).get("jobs", []):
            for row in job.get("events", []):
                safe = diagnostic_fields(row)
                assert not {"message", "error", "stack", "body", "query"} & safe.keys()
                totals["safe_log_diagnostics"] += safe.get("exception_type") is not None
    accepted_logs = 0
    generated = []
    for source in summaries:
        selected_summary = json.loads(source.read_text(encoding="utf-8-sig"))
        stage = selected_summary.get("stage")
        if not stage or not selected_summary.get("run_id") or "http" not in selected_summary:
            continue
        # 验证每个真实 run；不跨轮自动选择同档最近一次。
        selected = {stage: source.parent}
        scoped_logs = _load_logchecks(local / "evidence", selected)
        for row in scoped_logs.values():
            assert row.get("scope") == "run" and row.get("run_id") == selected_summary.get("run_id")
        accepted_logs += len(scoped_logs)
        assert _summary_table(selected)
        body = render_stage_report(deepcopy(selected_summary))
        assert "执行时间为总耗时减去排队时间" not in body
        assert "客户端侧可复现的检查均已执行" not in body
        target = destination / f"report-{source.parent.name}.md"
        target.write_text(body, encoding="utf-8")
        generated.append(target)
    assert not _load_logchecks(local / "evidence")
    assert not _render_unobserved_jobs(local / "evidence", {})
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in hashes.items())
    totals.update(generated_reports=len(generated), input_files_unchanged=len(hashes),
                  preserved_client_summaries=len(client_summaries),
                  real_logcheck_files=len(logchecks), accepted_scoped_logchecks=accepted_logs,
                  status="passed", scope="real_artifact_compatibility_only",
                  server_runtime_verified=False)
    (destination / "validation.json").write_text(json.dumps(totals, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**totals, "artifacts": str(destination)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
