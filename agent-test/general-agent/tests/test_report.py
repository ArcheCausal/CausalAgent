import csv
import json
from pathlib import Path

from general_agent_eval import report


def _case(path: Path, dataset: str, status: str, algorithm: str,
          duration: float, cost, tokens_in, tokens_out):
    case_dir = path / "cases" / dataset.replace("/", "__")
    case_dir.mkdir(parents=True)
    (case_dir / "case.json").write_text(json.dumps({
        "dataset": dataset,
        "mechanism": "M",
        "dim": 10,
        "n": 100,
        "status": status,
        "reason": "",
        "final_algorithm": algorithm,
        "duration_s": duration,
        "evidence": {"pre": True, "post": True},
        "metrics": {"f1": 0.5},
        "usage": {
            "tokens_input": tokens_in,
            "tokens_output": tokens_out,
            "total_cost_usd": cost,
        },
    }), encoding="utf-8")


def test_report_adds_algorithms_cost_and_failures(tmp_path: Path):
    run_dir = tmp_path / "r"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": "r",
        "runtime": {"mode": "docker"},
        "timeout_s": 600,
    }), encoding="utf-8")
    _case(run_dir, "M/D010_N100/rep00", "success", "GES (BDeu) + DirectLiNGAM orientation", 100, 0.5, 100, 50)
    _case(run_dir, "M/D010_N100/rep01", "success", "PC", 200, 0.25, 50, 25)
    _case(run_dir, "M/D010_N100/rep02", "invalid_answer", "", 30, 0.1, 10, 5)

    baseline = tmp_path / "baseline.csv"
    with baseline.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["dataset", "f1"])
        writer.writerow(["M/D010_N100/rep00", "0.5"])

    text = report.build(run_dir, baseline).read_text(encoding="utf-8")
    assert "## 算法选择" in text
    assert "| GES | 1 |" in text
    assert "| DirectLiNGAM | 1 |" in text
    assert "| PC | 1 |" in text
    assert "| （未识别） | 1 |" in text
    assert "| M | PC | 1 |" in text
    assert "## 成本与耗时" in text
    assert "token 输入 150" in text
    assert "$0.7500" in text
    assert "全部 case 成本合计（含未计分）：$0.8500" in text
    assert "| M/D010_N100/rep02 | invalid_answer |  | 30 | - |" in text
    assert "样本不一致：agent 计分 2 集 / 基线匹配 1 集" in text


def test_report_tolerates_missing_fields(tmp_path: Path):
    run_dir = tmp_path / "old"
    case_dir = run_dir / "cases" / "case-01"
    case_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"run_id": "old"}), encoding="utf-8")
    (case_dir / "case.json").write_text(json.dumps({
        "dataset": "M/D010_N100/rep00",
        "mechanism": "M",
        "status": "success",
        "metrics": {"f1": 0.5},
    }), encoding="utf-8")

    text = report.build(run_dir).read_text(encoding="utf-8")
    assert "计分集：1 / 1" in text
    assert "平均耗时 -s" in text
    assert "成本 $-" in text
    assert "未配置基线，跳过基线列。" in text
    assert "| 指标 | Agent | 基线 |" not in text
    assert "| 机制 | 集数 | 计分 | F1 | 基线 F1 | ΔF1 | SHD |" not in text


def test_baseline_prefers_scored_rows(tmp_path: Path):
    path = tmp_path / "baseline.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["dataset", "status", "f1"])
        writer.writerow(["X", "claude_error", ""])
        writer.writerow(["X", "success", "0.8"])
        writer.writerow(["Y", "claude_error", ""])

    rows = report._baseline(path)
    assert rows["X"]["status"] == "success"
    assert "Y" not in rows
