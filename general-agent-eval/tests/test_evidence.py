import json
from pathlib import Path

from general_agent_eval import data, runner


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
