"""无网络自检：python selftest.py"""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

import dataset as ds
from config import ConfigError, load_config


def _write_yaml(text: str) -> Path:
    handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
    handle.write(text)
    handle.close()
    return Path(handle.name)


BASE_YAML = """
run:
  base_url: http://localhost:5001
  parallel: 1
  seed: 7
data:
  dir: ./data
  per_cell: 1
output:
  dir: ./local/runs
"""


def test_load_valid_config():
    config = load_config(_write_yaml(BASE_YAML))
    assert config.run.base_url == "http://localhost:5001"
    assert config.run.parallel == 1
    assert config.run.timeout_minutes == 15
    assert config.data.per_cell == 1
    assert config.data.dir == Path("./data")


def test_missing_base_url_rejected():
    text = BASE_YAML.replace("  base_url: http://localhost:5001\n", "")
    try:
        load_config(_write_yaml(text))
    except ConfigError:
        return
    raise AssertionError("缺少 base_url 应报 ConfigError")


def test_per_cell_bounds():
    for value in ("0", "6"):
        try:
            load_config(_write_yaml(BASE_YAML.replace("per_cell: 1", f"per_cell: {value}")))
        except ConfigError:
            continue
        raise AssertionError(f"per_cell={value} 应报 ConfigError")


def test_parallel_must_be_positive():
    try:
        load_config(_write_yaml(BASE_YAML.replace("parallel: 1", "parallel: 0")))
    except ConfigError:
        return
    raise AssertionError("parallel=0 应报 ConfigError")


def test_scalar_mechanisms_rejected():
    text = BASE_YAML.replace("per_cell: 1", "per_cell: 1\n  mechanisms: TimeLag")
    try:
        load_config(_write_yaml(text))
    except ConfigError:
        return
    raise AssertionError("mechanisms 为标量应报 ConfigError")


def _make_tree(root: Path, names: list[str]) -> None:
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")


def test_scan_and_parse_paths():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_tree(root, [
            "CAM/D010_N00500/rep00.npz",
            "CAM/D010_N00500/rep01.npz",
            "Linear/D020_N01000/rep04.npz",
        ])
        specs = ds.scan(root)
        assert [spec.dataset_id for spec in specs] == [
            "CAM/D010_N00500/rep00",
            "CAM/D010_N00500/rep01",
            "Linear/D020_N01000/rep04",
        ]
        first = specs[0]
        assert (first.mechanism, first.dim_nominal, first.n_nominal, first.rep) == ("CAM", 10, 500, 0)
        assert ds.artifact_name(first.dataset_id) == "CAM__D010_N00500__rep00"


def test_select_is_deterministic_and_stratified():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_tree(root, [
            f"CAM/D010_N00500/rep0{index}.npz" for index in range(5)
        ] + [
            f"Linear/D010_N00500/rep0{index}.npz" for index in range(5)
        ])
        specs = ds.scan(root)
        first = ds.select(specs, None, (), (), (), 2, seed=11)
        second = ds.select(specs, None, (), (), (), 2, seed=11)
        assert [spec.dataset_id for spec in first] == [spec.dataset_id for spec in second]
        assert len(first) == 4
        assert {spec.mechanism for spec in first} == {"CAM", "Linear"}


def test_select_excludes_mechanism_and_filters():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_tree(root, [
            "TimeLag/D010_N00500/rep00.npz",
            "CAM/D015_N01000/rep00.npz",
        ])
        specs = ds.scan(root)
        assert ds.select(specs, ("TimeLag",), ("TimeLag",), (), (), 1, seed=1) == []
        picked = ds.select(specs, ("CAM",), (), (15,), (1000,), 1, seed=1)
        assert [spec.dataset_id for spec in picked] == ["CAM/D015_N01000/rep00"]


def run_tests() -> int:
    tests = [(name, value) for name, value in sorted(globals().items()) if name.startswith("test_")]
    failures = 0
    for name, test in tests:
        try:
            test()
            print(f"PASS {name}")
        except Exception:
            failures += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run_tests())
