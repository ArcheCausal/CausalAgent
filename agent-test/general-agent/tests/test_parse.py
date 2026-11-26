import numpy as np

from general_agent_eval.parse import parse_adjacency


def test_header_and_row_labels(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text(",X0,X1\nX0,0,1\nX1,0,0\n", encoding="utf-8")
    matrix, error = parse_adjacency(path, 2)
    assert error is None
    assert np.array_equal(matrix, np.array([[0, 1], [0, 0]]))


def test_boolean_cells(tmp_path):
    path = tmp_path / "b.csv"
    path.write_text("X0,false,true\nX1,false,false\n", encoding="utf-8")
    matrix, error = parse_adjacency(path, 2)
    assert error is None
    assert np.array_equal(matrix, np.array([[0, 1], [0, 0]]))


def test_extra_columns_are_truncated(tmp_path):
    path = tmp_path / "c.csv"
    path.write_text("0,1,0,9\n0,0,1,9\n0,0,0,9\n", encoding="utf-8")
    matrix, error = parse_adjacency(path, 3)
    assert error is None
    assert matrix.shape == (3, 3)


def test_missing_file(tmp_path):
    matrix, error = parse_adjacency(tmp_path / "none.csv", 3)
    assert matrix is None and error == "missing"


def test_wrong_shape(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text("0,1\n0,0\n", encoding="utf-8")
    matrix, error = parse_adjacency(path, 3)
    assert matrix is None and error == "shape"


def test_diagonal_must_be_zero(tmp_path):
    path = tmp_path / "e.csv"
    path.write_text("1,0\n0,0\n", encoding="utf-8")
    matrix, error = parse_adjacency(path, 2)
    assert matrix is None and error == "diagonal"


def test_non_binary_rejected(tmp_path):
    path = tmp_path / "f.csv"
    path.write_text("0,0.5\n0,0\n", encoding="utf-8")
    matrix, error = parse_adjacency(path, 2)
    assert matrix is None and error == "values"
