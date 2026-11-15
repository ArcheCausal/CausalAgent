from pathlib import Path

from general_agent_eval import data


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
