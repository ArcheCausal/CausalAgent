"""无网络自检：python selftest.py"""
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

import httpx
import numpy as np

import answer
import client as cl
import dataset as ds
import evaluate
import manifest
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


def test_f1_score():
    assert abs(evaluate.f1_score(1.0, 1.0) - 1.0) < 1e-9
    assert abs(evaluate.f1_score(0.5, 1.0) - 2 / 3) < 1e-9
    assert evaluate.f1_score(0.0, 0.0) == 0.0
    assert evaluate.f1_score(1.0, 0.0) == 0.0


def test_shd_cases():
    assert evaluate.shd([(0, 1, "directed")], {(0, 1)}) == 0
    assert evaluate.shd([(1, 2, "directed")], {(0, 1), (1, 2)}) == 1
    assert evaluate.shd([(0, 1, "directed"), (2, 3, "directed")], {(0, 1)}) == 1
    assert evaluate.shd([(1, 0, "directed")], {(0, 1)}) == 1
    assert evaluate.shd([(0, 1, "undirected")], {(0, 1)}) == 1
    assert evaluate.shd([(0, 1, "undirected")], {(1, 2)}) == 2
    assert evaluate.shd([(0, 1, "directed"), (1, 0, "directed")], {(0, 1)}) == 1
    assert evaluate.shd([(0, 0, "directed")], {(0, 1)}) == 2


def test_ranking_metrics():
    gold = {(0, 1)}
    perfect = evaluate.ranking_metrics([[0.0, 0.9], [0.1, 0.0]], gold)
    assert abs(perfect["auroc"] - 1.0) < 1e-9
    assert abs(perfect["auprc"] - 1.0) < 1e-9

    reversed_order = evaluate.ranking_metrics([[0.0, 0.1], [0.9, 0.0]], gold)
    assert abs(reversed_order["auroc"] - 0.0) < 1e-9
    assert abs(reversed_order["auprc"] - 0.5) < 1e-9

    tied = evaluate.ranking_metrics([[0.0, 0.5], [0.5, 0.0]], gold)
    assert abs(tied["auroc"] - 0.5) < 1e-9
    assert abs(tied["auprc"] - 0.5) < 1e-9


def test_probability_matrix_reorders_nodes():
    asset = {
        "metadata": {"score_type": "probability", "edge_score_matrix": [[0.0, 0.9], [0.1, 0.0]]},
        "nodes": [{"variable": "X1"}, {"variable": "X0"}],
    }
    matrix = evaluate.probability_matrix(asset, 2)
    assert matrix[1][0] == 0.9
    assert matrix[0][1] == 0.1
    assert evaluate.probability_matrix({"metadata": {}, "nodes": []}, 2) is None
    unmapped = {
        "metadata": {"score_type": "probability", "edge_score_matrix": [[0.0, 0.9], [0.1, 0.0]]},
        "nodes": [{"variable": "X0"}, {"variable": "Y"}],
    }
    assert evaluate.probability_matrix(unmapped, 2) is None


def test_probability_matrix_rejects_duplicate_nodes():
    duplicate = {
        "metadata": {"score_type": "probability", "edge_score_matrix": [[0.0, 0.9], [0.1, 0.0]]},
        "nodes": [{"variable": "X0"}, {"variable": "X0"}],
    }
    assert evaluate.probability_matrix(duplicate, 2) is None


def test_final_decision():
    records = [
        {"type": "decision", "payload": {"decision_kind": "algorithm", "summary": "调 PC"}},
        {"type": "decision", "payload": {"decision_kind": "final", "summary": "选 PC 为主结果", "confidence": "low"}},
    ]
    assert evaluate.final_decision(records) == ("选 PC 为主结果", "low")
    assert evaluate.final_decision([]) == ("", "")


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


def _mock_http(handler):
    test_client = cl.AgentClient("http://test")
    test_client.http = httpx.Client(
        base_url="http://test", transport=httpx.MockTransport(handler)
    )
    return test_client


def test_client_uses_caller_idempotency_key():
    seen = {}
    key = "11111111-1111-4111-8111-111111111111"

    def handler(request):
        seen["key"] = request.headers.get("Idempotency-Key")
        return httpx.Response(
            202,
            json={"success": True, "job_id": "j1", "status": "queued", "existing": False},
        )

    test_client = _mock_http(handler)
    try:
        assert test_client.create_job("s1", "msg", 7, key) == "j1"
    finally:
        test_client.close()
    assert seen["key"] == key


def test_client_resume_uses_caller_key():
    seen = {}
    key = "22222222-2222-4222-8222-222222222222"

    def handler(request):
        seen["key"] = request.headers.get("Idempotency-Key")
        return httpx.Response(
            202,
            json={"success": True, "job_id": "j1", "status": "queued", "existing": False},
        )

    test_client = _mock_http(handler)
    try:
        test_client.resume("j1", "q1", {"action": "confirm"}, key)
    finally:
        test_client.close()
    assert seen["key"] == key


def test_client_error_carries_status_and_payload():
    def handler(request):
        return httpx.Response(
            409,
            json={
                "success": False,
                "error": "conflict",
                "code": "active_job_conflict",
                "active_job": {"job_id": "j9"},
            },
        )

    test_client = _mock_http(handler)
    try:
        try:
            test_client.create_job(
                "s", "m", 1, "33333333-3333-4333-8333-333333333333"
            )
        except cl.ClientError as exc:
            assert exc.status == 409
            assert exc.payload["code"] == "active_job_conflict"
        else:
            raise AssertionError("409 应抛 ClientError")
    finally:
        test_client.close()


def test_client_find_active_job():
    def handler(request):
        assert request.url.path == "/api/agent/jobs/active"
        assert request.url.params["session_id"] == "s1"
        return httpx.Response(
            200,
            json={"success": True, "jobs": [{"job_id": "j2", "status": "running"}]},
        )

    test_client = _mock_http(handler)
    try:
        assert test_client.find_active_job("s1") == "j2"
    finally:
        test_client.close()


def _row(dataset_id="CAM/D010_N00500/rep00", recall=1.0, precision=0.5, status="success", reason=""):
    return {
        "run_id": "r1", "dataset": dataset_id, "mechanism": dataset_id.split("/")[0],
        "rep": 0, "n_rows": 500, "n_cols": 10, "dim": 10, "lambda": "",
        "n_gold_edges": 20, "n_pred_edges": 20, "n_undirected_edges": 0,
        "recall": recall, "precision": precision, "f1": 0.6, "shd": 3,
        "skeleton_recall": 0.8, "skeleton_precision": 0.7,
        "auroc": 0.75, "auprc": 0.65,
        "final_algorithm": "pc", "final_confidence": "low",
        "final_selection_rationale": "示例", "finalization_status": "valid",
        "interactions": 2, "duration_s": 120.0,
        "tokens_input": 1200, "tokens_output": 300, "tokens_total": 1500,
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
    assert abs(summary["f1_mean"] - 0.6) < 1e-9
    assert abs(summary["shd_mean"] - 3.0) < 1e-9
    assert abs(summary["auroc_mean"] - 0.75) < 1e-9
    assert summary["auroc_n"] == 1
    assert summary["tokens_total_mean"] == 1500
    assert summary["tokens_n"] == 2


def test_summary_tokens_skip_blank_rows():
    rows = [_row(), _row("Linear/D010_N00500/rep00", status="failed", reason="timeout")]
    rows[1]["tokens_input"] = ""
    rows[1]["tokens_output"] = ""
    rows[1]["tokens_total"] = ""
    summary = report.summarize(rows, {"sampling": {}})
    assert summary["tokens_total_mean"] == 1500
    assert summary["tokens_n"] == 1


def test_report_markdown_contains_token_columns():
    markdown = report.report_markdown([_row()], {"run_id": "r1", "git_commit": "abc"})
    assert "tokens 平均" in markdown
    assert "tokens(入/出/总)" in markdown


def test_summary_skips_missing_ranking_metrics():
    row = _row()
    row["auroc"] = ""
    row["auprc"] = ""
    summary = report.summarize([row], {"sampling": {"mechanisms": 1, "per_cell": 1, "seed": 1}})
    assert summary["auroc_mean"] == ""
    assert summary["auroc_n"] == 0
    assert abs(summary["f1_mean"] - 0.6) < 1e-9


def test_report_markdown_contains_tables():
    markdown = report.report_markdown([_row()], {"run_id": "r1", "git_commit": "abc"})
    assert "# 评测报告" in markdown
    assert "按机制" in markdown
    assert "pc" in markdown
    assert "F1" in markdown
    assert "SHD" in markdown
    assert "AUROC" in markdown


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


def test_usage_totals_sums_attempts_and_ignores_terminal_snapshot():
    records = [
        {"type": "usage", "payload": {"attempt": 1, "input_tokens": 10, "output_tokens": 2, "total_tokens": 12}},
        {"type": "usage", "payload": {"attempt": 1, "input_tokens": 15, "output_tokens": 5, "total_tokens": 20}},
        {
            "type": "final_result",
            "payload": {"attempt": 1, "usage": {"input_tokens": 15, "output_tokens": 5, "total_tokens": 20}},
        },
        {"type": "usage", "payload": {"attempt": 2, "input_tokens": 7, "output_tokens": 1, "total_tokens": 8}},
    ]
    assert rn.usage_totals(records) == {
        "tokens_input": 22,
        "tokens_output": 6,
        "tokens_total": 28,
    }
    assert rn.usage_totals([]) == {}
    assert rn.usage_totals([{"type": "usage", "payload": {"attempt": 1}}]) == {}


def test_add_usage_accumulates_jobs_and_skips_blank_rows():
    total = {}
    rn._add_usage(total, {"tokens_input": 10, "tokens_output": 2, "tokens_total": 12})
    rn._add_usage(total, {"tokens_input": "", "tokens_output": "", "tokens_total": ""})
    rn._add_usage(total, {"tokens_input": 5, "tokens_output": 1, "tokens_total": 6})
    assert total == {"tokens_input": 15, "tokens_output": 3, "tokens_total": 18}


def test_result_header_has_attempt_columns():
    assert rn.RESULT_HEADER[:6] == [
        "run_id", "dataset", "attempt", "job_id", "session_id", "mechanism",
    ]
    assert len(rn.RESULT_HEADER) == 33


def test_run_once_writes_attempt_suffixed_evidence():
    class FakeClient:
        def upload_csv(self, path): return 1
        def new_session(self): return "s"
        def create_job(self, session_id, message, user_file_id, idempotency_key): return "j"
        def resume(self, job_id, question_id, answer, idempotency_key): pass
        def find_active_job(self, session_id): return None
        def cancel(self, job_id, idempotency_key): pass
        def events(self, job_id, last_event_id=0):
            yield {"id": 1, "type": "final_result",
                   "payload": {"data": {"document": {"blocks": [], "assets": {}}}}}

    with tempfile.TemporaryDirectory() as tmp:
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
        row = rn._run_once(spec, config, FakeClient(), run_dir, "r1", attempt=2)
        base = run_dir / "raw" / "CAM__D010_N00500__rep00.attempt2"
        assert row["attempt"] == 2
        assert row["job_id"] == "j"
        assert row["session_id"] == "s"
        assert Path(str(base) + ".jsonl").exists()
        assert Path(str(base) + ".json").exists()


def _graph_payload(dim=2):
    nodes = [{"id": f"n{i}", "variable": f"X{i}"} for i in range(dim)]
    return {
        "data": {"document": {
            "report_mode": "analysis",
            "blocks": [{"type": "causal_graph", "asset_key": "g"}],
            "assets": {"g": {
                "graph_id": "g", "nodes": nodes,
                "edges": [{"source": "n0", "target": "n1",
                           "edge_type": "directed"}],
            }},
        }}
    }


def _state_config(root, run_dir):
    return rn.Config(
        run=rn.RunSettings(base_url="http://x", timeout_minutes=1),
        auth=rn.AuthSettings(), data=rn.DataSettings(dir=root),
        output=rn.OutputSettings(dir=run_dir))


def _state_spec_and_dirs(tmp):
    root = Path(tmp)
    _make_npz(root / "CAM/D010_N00500/rep00.npz",
              np.zeros((4, 2)), np.array([[0, 1], [0, 0]], dtype=np.int8))
    spec = ds.scan(root)[0]
    run_dir = root / "run"
    (run_dir / "csv").mkdir(parents=True)
    (run_dir / "raw").mkdir(parents=True)
    return spec, _state_config(root, run_dir), run_dir


def test_resume_retries_same_key_and_does_not_duplicate_answer():
    class FakeClient:
        def __init__(self):
            self.resume_calls = []
            self.event_calls = 0
        def upload_csv(self, path): return 1
        def new_session(self): return "s"
        def create_job(self, session_id, message, user_file_id, idempotency_key): return "j"
        def find_active_job(self, session_id): return None
        def cancel(self, job_id, idempotency_key): pass
        def resume(self, job_id, question_id, answer, idempotency_key):
            self.resume_calls.append((question_id, idempotency_key, answer))
            if len(self.resume_calls) == 1:
                raise cl.TransportError("boom")
        def events(self, job_id, last_event_id=0):
            self.event_calls += 1
            if self.event_calls in (1, 2):
                yield {"id": 1, "type": "interrupt", "payload": {
                    "question_id": "q1",
                    "interaction": {"interaction_type": "data_processing_question",
                                    "message": "数据是什么"},
                }}
            else:
                yield {"id": 2, "type": "final_result", "payload": _graph_payload()}

    with tempfile.TemporaryDirectory() as tmp:
        spec, config, run_dir = _state_spec_and_dirs(tmp)
        client = FakeClient()
        row = rn._run_once(spec, config, client, run_dir, "r1")
        assert row["status"] == "success"
        assert row["interactions"] == 1
        assert len(client.resume_calls) == 2
        assert client.resume_calls[0][0] == client.resume_calls[1][0] == "q1"
        assert client.resume_calls[0][1] == client.resume_calls[1][1]
        assert client.resume_calls[0][2] == client.resume_calls[1][2]
        requests_path = run_dir / "raw" / "CAM__D010_N00500__rep00.attempt1.requests.jsonl"
        entries = [json.loads(line) for line in requests_path.read_text(encoding="utf-8").splitlines()]
        resumes = [entry for entry in entries if entry["kind"] == "resume"]
        assert [entry["outcome"] for entry in resumes] == ["transport_error", "accepted"]
        assert resumes[0]["key"] == resumes[1]["key"]


def test_resume_conflict_fails_fast():
    class FakeClient:
        def __init__(self):
            self.cancelled = False
        def upload_csv(self, path): return 1
        def new_session(self): return "s"
        def create_job(self, session_id, message, user_file_id, idempotency_key): return "j"
        def find_active_job(self, session_id): return None
        def cancel(self, job_id, idempotency_key): self.cancelled = True
        def resume(self, job_id, question_id, answer, idempotency_key):
            raise cl.ClientError("conflict", 409, {
                "code": "job_state_conflict", "status": "failed"})
        def events(self, job_id, last_event_id=0):
            yield {"id": 1, "type": "interrupt", "payload": {
                "question_id": "q1",
                "interaction": {"interaction_type": "data_processing_question",
                                "message": "?"},
            }}

    with tempfile.TemporaryDirectory() as tmp:
        spec, config, run_dir = _state_spec_and_dirs(tmp)
        client = FakeClient()
        row = rn._run_once(spec, config, client, run_dir, "r1")
        assert row["failure_reason"] == "resume_rejected"
        assert client.cancelled is True


def test_interrupt_without_id_is_protocol_error():
    class FakeClient:
        def __init__(self):
            self.cancelled = False
        def upload_csv(self, path): return 1
        def new_session(self): return "s"
        def create_job(self, session_id, message, user_file_id, idempotency_key): return "j"
        def find_active_job(self, session_id): return None
        def cancel(self, job_id, idempotency_key): self.cancelled = True
        def resume(self, job_id, question_id, answer, idempotency_key): pass
        def events(self, job_id, last_event_id=0):
            yield {"id": None, "type": "interrupt", "payload": {
                "question_id": "q1", "interaction": {"interaction_type": "x"}}}

    with tempfile.TemporaryDirectory() as tmp:
        spec, config, run_dir = _state_spec_and_dirs(tmp)
        client = FakeClient()
        row = rn._run_once(spec, config, client, run_dir, "r1")
        assert row["failure_reason"] == "sse_error"
        assert client.cancelled is True


def test_create_transport_error_recovers_active_job():
    class FakeClient:
        def __init__(self):
            self.create_calls = 0
        def upload_csv(self, path): return 1
        def new_session(self): return "s"
        def create_job(self, session_id, message, user_file_id, idempotency_key):
            self.create_calls += 1
            raise cl.TransportError("lost response")
        def find_active_job(self, session_id): return "j-recovered"
        def cancel(self, job_id, idempotency_key): pass
        def resume(self, job_id, question_id, answer, idempotency_key): pass
        def events(self, job_id, last_event_id=0):
            yield {"id": 1, "type": "final_result", "payload": _graph_payload()}

    with tempfile.TemporaryDirectory() as tmp:
        spec, config, run_dir = _state_spec_and_dirs(tmp)
        client = FakeClient()
        row = rn._run_once(spec, config, client, run_dir, "r1")
        assert row["status"] == "success"
        assert row["job_id"] == "j-recovered"
        assert client.create_calls == 1


def test_create_retries_with_same_key():
    class FakeClient:
        def __init__(self):
            self.keys = []
        def upload_csv(self, path): return 1
        def new_session(self): return "s"
        def create_job(self, session_id, message, user_file_id, idempotency_key):
            self.keys.append(idempotency_key)
            if len(self.keys) < 3:
                raise cl.TransportError("boom")
            return "j3"
        def find_active_job(self, session_id): return None
        def cancel(self, job_id, idempotency_key): pass
        def resume(self, job_id, question_id, answer, idempotency_key): pass
        def events(self, job_id, last_event_id=0):
            yield {"id": 1, "type": "final_result", "payload": _graph_payload()}

    with tempfile.TemporaryDirectory() as tmp:
        spec, config, run_dir = _state_spec_and_dirs(tmp)
        client = FakeClient()
        row = rn._run_once(spec, config, client, run_dir, "r1")
        assert row["status"] == "success"
        assert len(client.keys) == 3
        assert len(set(client.keys)) == 1


def test_run_once_marks_missing_graph_failed():
    import tempfile as _tf
    class FakeClient:
        def upload_csv(self, path): return 1
        def new_session(self): return "s"
        def create_job(self, session_id, message, user_file_id, idempotency_key): return "j"
        def resume(self, job_id, question_id, answer, idempotency_key): pass
        def find_active_job(self, session_id): return None
        def events(self, job_id, last_event_id=0):
            yield {"id": 1, "type": "usage",
                   "payload": {"attempt": 1, "input_tokens": 10,
                               "output_tokens": 2, "total_tokens": 12}}
            yield {"id": 2, "type": "final_result",
                   "payload": {"data": {"document": {"blocks": [], "assets": {}}}}}
        def cancel(self, job_id, idempotency_key): pass
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
        assert (row["tokens_input"], row["tokens_output"], row["tokens_total"]) == (10, 2, 12)


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
            rows = rn._run_dataset(spec, object(), ("u", "p"), "rid", root / "run")
        finally:
            rn.AgentClient = original
        assert len(rows) == 1
        row = rows[0]
        assert row["status"] == "failed"
        assert row["failure_reason"] == "terminal_error"


def test_run_dataset_retry_failure_keeps_usage():
    calls = []

    def stub_run_once(spec, config, client, run_dir, run_id, attempt=1):
        calls.append(attempt)
        if attempt == 1:
            row = rn._failed_row(spec, run_id, "timeout", attempt=attempt)
            row.update({"tokens_input": 10, "tokens_output": 2, "tokens_total": 12})
            return row
        return rn._failed_row(spec, run_id, "upload_error", attempt=attempt)

    class FakeClient:
        def login(self, *credentials): pass
        def close(self): pass

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_npz(root / "CAM/D010_N00500/rep00.npz",
                  np.zeros((4, 2)), np.array([[0, 1], [0, 0]], dtype=np.int8))
        spec = ds.scan(root)[0]
        run_dir = root / "run"
        config = rn.Config(
            run=rn.RunSettings(base_url="http://x", timeout_minutes=1),
            auth=rn.AuthSettings(), data=rn.DataSettings(dir=root),
            output=rn.OutputSettings(dir=run_dir))
        original_client, original_run_once = rn.AgentClient, rn._run_once
        rn.AgentClient = lambda *a, **k: FakeClient()
        rn._run_once = stub_run_once
        try:
            rows = rn._run_dataset(spec, config, ("u", "p"), "r1", run_dir)
        finally:
            rn.AgentClient, rn._run_once = original_client, original_run_once
        assert len(rows) == 2
        row = rows[-1]
        assert calls == [1, 2]
        assert row["failure_reason"] == "upload_error"
        assert row["status"] == "failed"
        assert (row["tokens_input"], row["tokens_output"], row["tokens_total"]) == (10, 2, 12)


def test_run_dataset_writes_one_row_per_attempt():
    calls = []

    def stub_run_once(spec, config, client, run_dir, run_id, attempt=1):
        calls.append(attempt)
        if attempt == 1:
            row = rn._failed_row(spec, run_id, "timeout", attempt=attempt)
            row.update({"tokens_input": 10, "tokens_output": 2, "tokens_total": 12})
            return row
        row = rn._failed_row(spec, run_id, "", attempt=attempt)
        row.update({"status": "success", "tokens_input": 5,
                    "tokens_output": 1, "tokens_total": 6})
        return row

    class FakeClient:
        def login(self, *credentials): pass
        def close(self): pass

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_npz(root / "CAM/D010_N00500/rep00.npz",
                  np.zeros((4, 2)), np.array([[0, 1], [0, 0]], dtype=np.int8))
        spec = ds.scan(root)[0]
        run_dir = root / "run"
        config = _state_config(root, run_dir)
        original_client, original_run_once = rn.AgentClient, rn._run_once
        rn.AgentClient = lambda *a, **k: FakeClient()
        rn._run_once = stub_run_once
        try:
            rows = rn._run_dataset(spec, config, ("u", "p"), "r1", run_dir)
        finally:
            rn.AgentClient, rn._run_once = original_client, original_run_once
        assert len(rows) == 2
        assert calls == [1, 2]
        assert rows[0]["attempt"] == 1
        assert rows[1]["attempt"] == 2
        assert rows[1]["tokens_total"] == 18


def _manifest_fixture(tmp):
    root = Path(tmp)
    _make_npz(root / "CAM/D010_N00500/rep00.npz",
              np.zeros((4, 2)), np.array([[0, 1], [0, 0]], dtype=np.int8))
    specs = ds.scan(root)
    config = load_config(_write_yaml(BASE_YAML))
    return root, specs, config


def test_manifest_roundtrip_and_mismatches():
    with tempfile.TemporaryDirectory() as tmp:
        root, specs, config = _manifest_fixture(tmp)
        data = manifest.build(config, specs, "r1", "abc")
        path = root / "manifest.json"
        manifest.write(path, data)
        loaded = manifest.read(path)
        assert loaded is not None
        assert manifest.verify(loaded, config, specs, "abc") == []
        assert manifest.check(loaded, config, specs, "abc") == []
        assert manifest.read(root / "missing.json") is None
        (root / "broken.json").write_text('{"run_id": ', encoding="utf-8")
        assert manifest.read(root / "broken.json") is None

        bad = dict(loaded)
        bad["datasets"] = [dict(loaded["datasets"][0], sha256="bad")]
        problems = manifest.check(bad, config, specs, "abc", force=True)
        assert any("sha256" in item for item in problems)
        try:
            manifest.check(bad, config, specs, "abc")
        except manifest.ManifestError:
            pass
        else:
            raise AssertionError("不一致且未 force 时应抛 ManifestError")


def test_manifest_detects_config_commit_and_version_changes():
    with tempfile.TemporaryDirectory() as tmp:
        root, specs, config = _manifest_fixture(tmp)
        data = manifest.build(config, specs, "r1", "abc")
        assert any("git_commit" in item
                   for item in manifest.verify(data, config, specs, "def"))
        changed = manifest.build(
            load_config(_write_yaml(BASE_YAML.replace(
                "base_url: http://localhost:5001", "base_url: http://other:5001"))),
            specs, "r1", "abc")
        assert any("base_url" in item
                   for item in manifest.verify(changed, config, specs, "abc"))
        data["metrics_version"] = -1
        assert any("metrics_version" in item
                   for item in manifest.verify(data, config, specs, "abc"))


def test_manifest_gate_blocks_and_allows_force():
    with tempfile.TemporaryDirectory() as tmp:
        root, specs, config = _manifest_fixture(tmp)
        run_dir = root / "run"
        run_dir.mkdir()
        try:
            rn._manifest_gate(run_dir, config, specs, "abc", False)
        except manifest.ManifestError:
            pass
        else:
            raise AssertionError("缺少清单应抛 ManifestError")

        data = manifest.build(config, specs, "r1", "abc")
        data["datasets"][0]["sha256"] = "bad"
        manifest.write(run_dir / "manifest.json", data)
        try:
            rn._manifest_gate(run_dir, config, specs, "abc", False)
        except manifest.ManifestError:
            pass
        else:
            raise AssertionError("不一致且未 force 应抛 ManifestError")
        problems = rn._manifest_gate(run_dir, config, specs, "abc", True)
        assert any("sha256" in item for item in problems)


def test_report_renders_identity_mismatches():
    markdown = report.report_markdown(
        [_row()],
        {"run_id": "r1", "git_commit": "abc",
         "identity_mismatches": ["git_commit: 'a' -> 'b'"]},
    )
    assert "续跑身份差异" in markdown
    assert "git_commit: 'a' -> 'b'" in markdown


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
