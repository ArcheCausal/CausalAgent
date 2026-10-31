"""无网络自检：python selftest.py"""
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

import numpy as np

import answer
import client as cl
import dataset as ds
import evaluate
import report
import runner as rn
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


def _make_npz(path: Path, X, adjacency) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, X=X, adjacency=adjacency)


def test_load_write_profile_and_gold():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        X = np.array([[1, 0.5], [2, 0.25], [3, 1.0]], dtype=np.float32)
        adjacency = np.array([[0, 1], [0, 0]], dtype=np.int8)
        _make_npz(root / "CAM/D010_N00500/rep00.npz", X, adjacency)
        spec = ds.scan(root)[0]
        loaded, gold = ds.load(spec)
        assert loaded.shape == (3, 2)
        assert ds.gold_edges(gold) == {(0, 1)}

        out = root / "upload.csv"
        ds.write_csv(loaded, out)
        lines = out.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "X0,X1"
        assert lines[1] == "1,0.5"

        report = ds.profile(loaded)
        assert report["n_rows"] == 3
        assert report["missing"] is False
        assert report["uniques"] == [3, 3]


def test_load_rejects_missing_keys():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "CAM/D010_N00500/rep00.npz"
        path.parent.mkdir(parents=True)
        np.savez(path, X=np.zeros((3, 2)))
        spec = ds.scan(Path(tmp))[0]
        try:
            ds.load(spec)
        except ds.DatasetInvalid:
            return
        raise AssertionError("缺少 adjacency 应报 DatasetInvalid")


def _plan_interaction(dim=2, transformations=("standardize",)):
    return {
        "interaction_type": "data_processing_plan_confirmation",
        "plan_version": "a" * 64,
        "plan": {
            "variable_semantics": {f"X{index}": f"变量{index}" for index in range(dim)},
            "transformations": list(transformations),
        },
    }


def test_answer_confirm_plan():
    profile = {"n_rows": 10, "n_cols": 2, "kinds": ["numeric", "binary"], "missing": False,
               "ranges": [(0.0, 1.0), (0.0, 1.0)], "uniques": [10, 2]}
    payload, kind = answer.answer_for({"interaction": _plan_interaction()}, profile)
    assert kind == "confirm"
    assert payload == {"action": "confirm", "plan_version": "a" * 64}


def test_answer_rejects_destructive_plan():
    profile = {"n_rows": 10, "n_cols": 2, "kinds": ["numeric", "numeric"], "missing": False,
               "ranges": [(0.0, 1.0), (0.0, 1.0)], "uniques": [10, 10]}
    payload, kind = answer.answer_for({"interaction": _plan_interaction(2, ("drop", "standardize"))}, profile)
    assert kind == "reject"
    assert isinstance(payload, str) and "删除" in payload


def test_answer_free_text_uses_profile():
    profile = {"n_rows": 500, "n_cols": 3, "kinds": ["numeric"] * 3, "missing": False,
               "ranges": [(0.0, 1.0)] * 3, "uniques": [100] * 3}
    payload, kind = answer.answer_for(
        {"interaction": {"interaction_type": "data_processing_question", "message": "数据是什么"}},
        profile,
    )
    assert kind == "profile"
    assert "500" in payload and "X0" in payload and "X2" in payload


def test_answer_rejects_scalar_transformation():
    profile = {"n_rows": 10, "n_cols": 2, "kinds": ["numeric", "numeric"], "missing": False,
               "ranges": [(0.0, 1.0), (0.0, 1.0)], "uniques": [10, 10]}
    interaction = _plan_interaction()
    interaction["plan"]["transformations"] = "drop"
    payload, kind = answer.answer_for({"interaction": interaction}, profile)
    assert kind == "reject" and isinstance(payload, str)


def test_answer_survives_odd_payloads():
    profile = {"n_rows": 10, "n_cols": 2, "kinds": ["numeric", "numeric"], "missing": False,
               "ranges": [(0.0, 1.0), (0.0, 1.0)], "uniques": [10, 10]}
    for interrupt in (None, {}, {"interaction": "oops"}, {"interaction": ["x"]},
                      {"interaction": {"interaction_type": "data_processing_plan_confirmation", "plan": []}}):
        payload, kind = answer.answer_for(interrupt, profile)
        assert isinstance(payload, (str, dict)) and kind in ("confirm", "reject", "profile")


def _asset(edges, variables=("X0", "X1", "X2")):
    return {
        "graph_id": "graph_main",
        "metadata": {"algorithm": "pc"},
        "nodes": [
            {"id": f"n{index}", "variable": variable}
            for index, variable in enumerate(variables)
        ],
        "edges": edges,
    }


def test_find_graph_via_block_and_diagnostic():
    asset = _asset([])
    document = {
        "blocks": [{"type": "section", "children": [{"type": "causal_graph", "asset_key": "graph_main"}]}],
        "assets": {"graph_main": asset},
        "report_mode": "analysis",
    }
    found, reason = evaluate.find_graph_asset(document)
    assert found is asset and reason is None
    found, reason = evaluate.find_graph_asset({**document, "report_mode": "diagnostic"})
    assert found is None and reason == "diagnostic_report"
    found, reason = evaluate.find_graph_asset({"blocks": [], "assets": {}})
    assert found is None and reason == "no_causal_graph"


def test_parse_edges_maps_node_ids_and_flags_unmapped():
    asset = _asset([
        {"source": "n0", "target": "n1", "edge_type": "directed"},
        {"source": "n1", "target": "n2", "edge_type": "undirected"},
        {"source": "n0", "target": "ghost", "edge_type": "directed"},
    ])
    edges, unmapped = evaluate.parse_edges(asset)
    assert edges == [(0, 1, "directed"), (1, 2, "undirected")]
    assert unmapped is True


def test_score_strict_and_skeleton():
    gold = {(0, 1), (1, 2)}
    edges = [(0, 1, "directed"), (1, 0, "directed"), (2, 1, "directed"), (2, 0, "undirected")]
    metrics = evaluate.score(edges, gold)
    assert metrics["n_pred_edges"] == 1
    assert metrics["n_undirected_edges"] == 2
    assert metrics["recall"] == 0.0
    assert metrics["skeleton_recall"] == 1.0


def test_score_empty_graph():
    metrics = evaluate.score([], {(0, 1)})
    assert metrics["recall"] == 0.0 and metrics["precision"] == 0.0
    assert metrics["n_pred_edges"] == 0 and metrics["n_undirected_edges"] == 0


def test_score_mirror_counts_in_skeleton():
    metrics = evaluate.score([(0, 1, "directed"), (1, 0, "directed")], {(0, 1)})
    assert metrics["n_pred_edges"] == 0
    assert metrics["n_undirected_edges"] == 1
    assert metrics["recall"] == 0.0
    assert metrics["skeleton_recall"] == 1.0
    assert metrics["skeleton_precision"] == 1.0


def test_score_each_undirected_edge_counts_as_fp():
    metrics = evaluate.score(
        [(0, 1, "undirected"), (0, 1, "bidirected"), (1, 2, "partially_directed")],
        {(0, 1)},
    )
    assert metrics["n_undirected_edges"] == 3
    assert metrics["precision"] == 0.0
    assert metrics["skeleton_recall"] == 1.0


def test_parse_sse_events():
    lines = [
        "id: 7",
        "event: interrupt",
        'data: {"message": "hi", "question_id": "q1", "interaction": {"interaction_type": "data_processing_question"}}',
        "",
        "id: 8",
        "event: heartbeat",
        'data: {"job_id": "j1"}',
        "",
    ]
    events = list(cl.parse_sse(lines))
    assert events[0][0] == 7
    assert events[0][1] == "interrupt"
    assert events[0][2]["question_id"] == "q1"
    assert events[1][1] == "heartbeat"


def test_parse_sse_ignores_heartbeat_comments():
    assert list(cl.parse_sse([": keep-alive", ""])) == []


def _row(dataset_id="CAM/D010_N00500/rep00", recall=1.0, precision=0.5, status="success", reason=""):
    return {
        "run_id": "r1", "dataset": dataset_id, "mechanism": dataset_id.split("/")[0],
        "rep": 0, "n_rows": 500, "n_cols": 10, "dim": 10, "lambda": "",
        "n_gold_edges": 20, "n_pred_edges": 20, "n_undirected_edges": 0,
        "recall": recall, "precision": precision, "skeleton_recall": 0.8,
        "skeleton_precision": 0.7, "final_algorithm": "pc", "finalization_status": "valid",
        "interactions": 2, "duration_s": 120.0, "tokens": "not_collected",
        "status": status, "failure_reason": reason,
    }


def test_summarize_counts_and_means():
    rows = [_row(), _row("Linear/D010_N00500/rep00", recall=0.0, status="failed", reason="timeout")]
    summary = report.summarize(rows, {"sampling": {"mechanisms": 2, "per_cell": 1, "seed": 1}})
    assert summary["datasets"] == 2
    assert summary["succeeded"] == 1
    assert summary["failed"] == 1
    assert abs(summary["recall_mean"] - 1.0) < 1e-9
    assert summary["failure_reasons"] == "timeout:1"


def test_report_markdown_contains_tables():
    markdown = report.report_markdown([_row()], {"run_id": "r1", "git_commit": "abc"})
    assert "# 评测报告" in markdown
    assert "按机制" in markdown
    assert "pc" in markdown


def _resume_rows():
    return [
        {"dataset": "CAM/D010_N00500/rep00", "status": "failed", "failure_reason": "sse_error",
         "final_algorithm": "", "recall": 0, "precision": 0, "skeleton_recall": 0,
         "skeleton_precision": 0, "duration_s": 0.0, "interactions": 0,
         "mechanism": "CAM", "dim": 10, "n_rows": 500},
        {"dataset": "CAM/D010_N00500/rep00", "status": "success", "failure_reason": "",
         "final_algorithm": "pc", "recall": 0.9, "precision": 0.8, "skeleton_recall": 1.0,
         "skeleton_precision": 0.95, "duration_s": 120.0, "interactions": 2,
         "mechanism": "CAM", "dim": 10, "n_rows": 500},
    ]


def test_summarize_dedupes_resumed_rows():
    summary = report.summarize(_resume_rows(), {"sampling": {}})
    assert summary["datasets"] == 1
    assert summary["succeeded"] == 1
    assert summary["failed"] == 0
    assert summary["failure_reasons"] == ""


def test_report_markdown_dedupes_resumed_rows():
    markdown = report.report_markdown(_resume_rows(), {"run_id": "r1", "git_commit": "abc"})
    failures = markdown.split("## 失败明细", 1)[1].split("## recall 最低", 1)[0]
    assert "CAM/D010_N00500/rep00" not in failures
    assert "数据集: 1" in markdown


def test_scalar_exclude_rejected():
    text = BASE_YAML.replace("per_cell: 1", "per_cell: 1\n  exclude: TimeLag")
    try:
        load_config(_write_yaml(text))
    except ConfigError:
        return
    raise AssertionError("exclude 为标量应报 ConfigError")


def test_build_prompt():
    assert rn.build_prompt(10) == "请读取上传的 CSV 数据文件，对变量 X0 到 X9 执行因果分析"


def test_retryable_set():
    assert rn.RETRYABLE == {"timeout", "terminal_error", "no_causal_graph"}


def test_validate_selection_rejects_unknown_values():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_tree(root, ["CAM/D010_N00500/rep00.npz"])
        specs = ds.scan(root)
        for mechanisms, dims, ns in (
            (("Nope",), (), ()),
            (None, (99,), ()),
            (None, (), (99999,)),
        ):
            try:
                rn.validate_selection(specs, mechanisms, dims, ns)
            except ConfigError:
                continue
            raise AssertionError(f"未知取值应报 ConfigError: {mechanisms} {dims} {ns}")


def test_run_once_marks_missing_graph_failed():
    import tempfile as _tf
    class FakeClient:
        def upload_csv(self, path): return 1
        def new_session(self): return "s"
        def create_job(self, session_id, message, user_file_id): return "j"
        def events(self, job_id, last_event_id=0):
            yield {"id": 1, "type": "final_result",
                   "payload": {"data": {"document": {"blocks": [], "assets": {}}}}}
        def cancel(self, job_id): pass
    with _tf.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_npz(root / "CAM/D010_N00500/rep00.npz",
                  np.zeros((4, 2)), np.array([[0, 1], [0, 0]], dtype=np.int8))
        spec = ds.scan(root)[0]
        run_dir = root / "run"
        (run_dir / "csv").mkdir(parents=True)
        (run_dir / "raw").mkdir(parents=True)
        config = rn.Config(
            run=rn.RunSettings(base_url="http://x", timeout_minutes=1),
            auth=rn.AuthSettings(), data=rn.DataSettings(dir=root),
            output=rn.OutputSettings(dir=run_dir))
        row = rn._run_once(spec, config, FakeClient(), run_dir, "r1")
        assert row["status"] == "failed" and row["failure_reason"] == "no_causal_graph"


def test_run_dataset_corrupt_npz_returns_terminal_error():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "CAM/D010_N00500/rep00.npz"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"not a real npz file")
        spec = ds.scan(root)[0]
        original = rn.AgentClient
        rn.AgentClient = lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应构造客户端"))
        try:
            row = rn._run_dataset(spec, object(), ("u", "p"), "rid", root / "run")
        finally:
            rn.AgentClient = original
        assert row["status"] == "failed"
        assert row["failure_reason"] == "terminal_error"


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
