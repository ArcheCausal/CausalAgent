import json
import signal
from pathlib import Path

from general_agent_eval import data, report, runner


def _write_harness(case_dir: Path, name: str, packages: list[str], **extra):
    harness = case_dir / ".harness"
    harness.mkdir(parents=True, exist_ok=True)
    payload = {"pip_freeze": packages, **extra}
    (harness / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_dataset_sha256_is_deterministic_and_content_sensitive(tmp_path: Path):
    root = tmp_path / "bench"
    root.mkdir()
    npz = root / "a.npz"
    npz.write_bytes(b"one")
    first = data.dataset_sha256(root)
    assert data.dataset_sha256(root) == first
    npz.write_bytes(b"two")
    assert data.dataset_sha256(root) != first


def test_toolbox_lock_path_exists():
    assert data.toolbox_lock_path().is_file()


def test_env_drift_detects_freeze_change(tmp_path: Path):
    same = tmp_path / "case-01"
    changed = tmp_path / "case-02"
    missing = tmp_path / "case-03"
    _write_harness(same, "pre", ["numpy==1", "pandas==2"])
    _write_harness(same, "post", ["pandas==2", "numpy==1"])
    _write_harness(changed, "pre", ["numpy==1"])
    _write_harness(changed, "post", ["numpy==2"])
    _write_harness(missing, "pre", ["numpy==1"])

    assert runner.env_drift(
        [("case-01", same), ("case-02", changed), ("case-03", missing)]
    ) == ["case-02"]


def test_finish_enriches_run_json(tmp_path: Path):
    run_dir = tmp_path / "runs" / "r"
    case_dir = run_dir / "cases" / "case-01"
    (case_dir / ".harness").mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": "r",
        "agent": {"claude_version": None},
        "toolbox": {"pre_freeze_sha256": None},
        "env_drift_cases": [],
    }), encoding="utf-8")
    (case_dir / "case.json").write_text(json.dumps({
        "dataset": "M/D010_N100/rep00",
        "case": "case-01",
        "status": "success",
    }), encoding="utf-8")
    _write_harness(
        case_dir, "pre", ["pandas==2", "numpy==1"], claude="2.1.322 (Claude Code)"
    )
    _write_harness(case_dir, "post", ["numpy==1", "pandas==2"])

    runner._finish(run_dir)
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert meta["agent"]["claude_version"] == "2.1.322 (Claude Code)"
    assert meta["toolbox"]["pre_freeze_sha256"]
    assert meta["env_drift_cases"] == []
    assert meta["status_counts"] == {"success": 1}


def test_report_shows_missing_evidence_and_unknown_fields(tmp_path: Path):
    run_dir = tmp_path / "r"
    case_dir = run_dir / "cases" / "case-01"
    case_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": "r",
        "runtime": {"mode": "docker"},
        "timeout_s": 600,
        "env_drift_cases": ["case-02"],
    }), encoding="utf-8")
    (case_dir / "case.json").write_text(json.dumps({
        "dataset": "M/D010_N100/rep00",
        "mechanism": "M",
        "dim": 10,
        "n": 100,
        "case": "case-01",
        "status": "success",
        "evidence": {"pre": True, "post": False},
        "metrics": {},
    }), encoding="utf-8")

    path = report.build(run_dir)
    text = path.read_text(encoding="utf-8")
    assert "环境证据" in text
    assert "case-01" in text
    assert "case-02" in text
    assert "未知" in text


def test_docker_oom_is_not_final():
    assert "docker_oom" not in runner.FINAL


def test_algorithms_detects_all_and_final_prefers_answer(tmp_path: Path):
    events = [{"message": {"content": [
        {"type": "tool_use", "input": {"code": "from causallearn.search.ConstraintBased.PC import pc"}},
        {"type": "tool_use", "input": {"code": "directlingam"}},
    ]}}]
    assert runner._algorithms(events) == "DirectLiNGAM;PC"

    case_dir = tmp_path / "case"
    (case_dir / "answer").mkdir(parents=True)
    assert runner._final_algorithm(case_dir, "DirectLiNGAM;PC") == "DirectLiNGAM;PC"

    (case_dir / "answer" / "algorithm.txt").write_text(
        "GES (BDeu) + DirectLiNGAM orientation\n", encoding="utf-8"
    )
    assert runner._final_algorithm(case_dir, "DirectLiNGAM;PC") == "GES (BDeu) + DirectLiNGAM orientation"


def test_interrupt_carries_signal_number():
    try:
        runner._raise_interrupt(signal.SIGTERM, None)
    except runner._Interrupted as exc:
        assert exc.signum == signal.SIGTERM
    else:
        raise AssertionError("应抛出 _Interrupted")
