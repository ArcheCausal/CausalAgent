"""只读取现存真实产物，验证资源汇总、清单导出和主库附加兼容性。

不访问服务器，不启动负载，不构造业务 summary。新产物写入 local/evidence/<date>/
独立验证目录；原始文件只校验哈希，不修改。
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from ops.server_evidence import manifest
from runner import config, resource
from runner.evidence import (_job_ids, _summary_table, build_evidence,
                             _load_logchecks, _render_unobserved_jobs,
                             export_request_manifest, latest_runs, merge_job_db_check)


def main() -> int:
    local = BASE / "local"
    # 默认校验最近一个有运行的日期目录；没有历史运行时才用当天。
    date_dir = config.latest_run_date() or config.date_key(time.time())
    runs_dir = local / "runs" / date_dir
    evidence_dir = local / "evidence" / date_dir
    if not runs_dir.is_dir():
        print(f"没有可校验的运行目录：{runs_dir}")
        return 2
    destination = local / "evidence" / date_dir / ("server-evidence-validation-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    destination.mkdir(parents=True, exist_ok=False)
    # 只校验所选日期目录，避免把不同日期的摘要混成一份兼容性结论。
    summaries = sorted(runs_dir.glob("*/summary.json"))
    samples = sorted(set(runs_dir.glob("*sample*.json"))
                     | set(runs_dir.glob("*/*sample*.json")))
    databases = sorted(set(local.glob("job-db-*.json"))
                       | set((local / "evidence").glob("*/job-db-*.json"))
                       | set((local / "evidence").glob("job-db-*.json")))
    logchecks = sorted((local / "evidence").glob("*/logcheck-*.json"))
    inputs = summaries + samples + databases + logchecks
    hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
    db_by_run = {}
    for path in databases:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if payload.get("run_id"):
            db_by_run[payload["run_id"]] = payload
    totals = {"real_summaries": 0, "real_resource_files": 0, "real_resource_samples": 0,
              "manifest_exports": 0, "matching_db_merges": 0, "cross_run_rejections": 0,
              "unscoped_db_rejections": 0, "legacy_v1_db_merges": 0, "legacy_v1_job_rows": 0}
    for path in summaries:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not payload.get("run_id") or "http" not in payload:
            continue
        totals["real_summaries"] += 1
        original = deepcopy(payload)
        exported = export_request_manifest(payload, destination / f"requests-{path.parent.name}.json")
        listing = manifest(str(exported), payload["run_id"])
        assert listing["run_id"] == payload["run_id"]
        assert listing["job_ids"] == _job_ids(payload)
        assert len(listing["requests"]) == len(payload.get("requests", []))
        assert payload == original
        totals["manifest_exports"] += 1
        db = db_by_run.get(payload["run_id"])
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
        assert "额外服务器观察" in rendered
        if not payload.get("stop_submitting_at") and not payload.get("submission_stopped_at"):
            assert "| 负载 | 未采集 |" in rendered
            assert "| 客户端排空 | 未采集 |" in rendered
        totals["real_resource_files"] += 1
        totals["real_resource_samples"] += summary["sample_count"]
    selected = latest_runs(runs_dir)
    scoped_logs = _load_logchecks(evidence_dir)
    for stage, row in scoped_logs.items():
        selected_summary = json.loads((selected[stage] / "summary.json").read_text(encoding="utf-8"))
        assert row.get("scope") == "run" and row.get("run_id") == selected_summary.get("run_id")
    assert all(row.get("scope") == "run" and row.get("run_id") for row in scoped_logs.values())
    assert not _render_unobserved_jobs(evidence_dir, {})
    table = "\n".join(_summary_table(selected))
    assert "已完成样本排队 p95 ms" in table and "cdfm_fixed" in table and "cdfm_rate" in table
    combined, generated = build_evidence(runs_dir, destination / "reports")
    body = combined.read_text(encoding="utf-8")
    assert "执行时间为总耗时减去排队时间" not in body
    assert "客户端侧可复现的检查均已执行" not in body
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in hashes.items())
    totals.update(generated_reports=len(generated), input_files_unchanged=len(hashes),
                  real_logcheck_files=len(logchecks), accepted_scoped_logchecks=len(scoped_logs),
                  status="passed", scope="real_artifact_compatibility_only",
                  server_runtime_verified=False)
    (destination / "validation.json").write_text(json.dumps(totals, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**totals, "artifacts": str(destination)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
