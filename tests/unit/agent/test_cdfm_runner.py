"""CDFM runner 的纯输入与矩阵方向契约。"""

from __future__ import annotations

import numpy as np

from Agent.causal.cdfm_runner import _parse_csv, adjacency_to_edges, run_cdfm_analysis


def test_adjacency_to_edges_preserves_row_to_column_direction_and_cycles() -> None:
    edges = adjacency_to_edges(
        np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]], dtype=np.int8),
        ("A", "B", "C"),
    )

    assert edges == [
        {"from": "A", "to": "B", "arrows": "to"},
        {"from": "B", "to": "C", "arrows": "to"},
        {"from": "C", "to": "A", "arrows": "to"},
    ]


def test_parse_csv_retains_cdfm_missing_mask_without_filling_values() -> None:
    data, names, missing_mask = _parse_csv("A,B\n1,2\n,3\n")

    assert names == ["A", "B"]
    assert np.isnan(data[1, 0])
    assert missing_mask.tolist() == [[False, False], [True, False]]


def test_runner_rejects_input_before_model_loading() -> None:
    short = run_cdfm_analysis("A,B\n1,2\n")
    categorical = run_cdfm_analysis("A,B\na,2\nb,3\n")
    invalid_threshold = run_cdfm_analysis("A,B\n1,2\n3,4\n", threshold=1.5)

    assert short["error_type"] == "InputValidationError"
    assert categorical["error_type"] == "InputValidationError"
    assert invalid_threshold["error_type"] == "InputValidationError"
