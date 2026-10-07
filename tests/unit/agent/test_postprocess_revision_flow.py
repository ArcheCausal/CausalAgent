import asyncio
import os
import sys
import types

import numpy as np
import pytest


for key, value in {
    "SECRET_KEY": "test-secret",
    "API_KEY": "test-api-key",
    "BASE_URL": "https://example.test",
    "MODEL": "test-model",
    "MYSQL_HOST": "mysql",
    "MYSQL_USER": "app",
    "MYSQL_PASSWORD": "password",
    "MYSQL_DATABASE": "causalagent",
}.items():
    os.environ.setdefault(key, value)


def _install_import_stubs():
    """隔离后处理测试不需要的数据库和向量库依赖。"""
    agent_connect = types.ModuleType("Database.agent_connect")
    agent_connect.require_frozen_file_for_job = lambda *args, **kwargs: None
    sys.modules["Database.agent_connect"] = agent_connect

_install_import_stubs()

from Agent.Postprocessing.cycles_check import fix_cycles
from Agent.Postprocessing.cycles_check.detect_cycles import detect_cycles
from Agent.Postprocessing.evaluate_edge.evaluate_edge_llm import (
    EdgeEvaluationResult,
    _apply_edge_decisions,
)
from Agent.Postprocessing.evaluate_edge import evaluate_edge_llm
from Agent.llm_structured_output import StructuredOutputError
from Agent.causal_agent import nodes


NODE_NAMES = ["A", "B", "C"]
GRAPH_NODES = [{"id": name, "label": name} for name in NODE_NAMES]
GRAPH_EDGES = [
    {"from": "A", "to": "B", "arrows": "to", "label": "ab"},
    {"from": "B", "to": "C", "arrows": "to", "label": "bc"},
    {"from": "C", "to": "A", "arrows": "to", "label": "ca"},
]
PC_CYCLE_MATRIX = np.array(
    [
        [0, 0, 1],
        [1, 0, 0],
        [0, 1, 0],
    ]
)
OLC_CYCLE_MATRIX = np.array(
    [
        [0, 1, 0],
        [0, 0, 1],
        [1, 0, 0],
    ]
)
DIRECT_LINGAM_CYCLE_MATRIX = np.array(
    [
        [0.0, 0.0, 0.7],
        [-0.4, 0.0, 0.0],
        [0.0, 1.2, 0.0],
    ]
)


def _cycle_edges(cycle):
    """把 networkx 环路节点序列转换为有向边集合。"""
    return {
        (cycle[index], cycle[(index + 1) % len(cycle)])
        for index in range(len(cycle))
    }


@pytest.mark.parametrize(
    ("matrix", "matrix_convention"),
    [
        (PC_CYCLE_MATRIX, "causallearn"),
        (DIRECT_LINGAM_CYCLE_MATRIX, "target_to_source"),
        (OLC_CYCLE_MATRIX, "olc"),
    ],
)
def test_detect_cycles_respects_matrix_direction(matrix, matrix_convention):
    """PC 与 OLC 的矩阵必须被解释成同一个 A→B→C→A 环路。"""
    has_cycle, cycles = detect_cycles(
        matrix,
        NODE_NAMES,
        matrix_convention=matrix_convention,
    )

    assert has_cycle is True
    assert any(
        _cycle_edges(cycle) == {("A", "B"), ("B", "C"), ("C", "A")}
        for cycle in cycles
    )


@pytest.mark.parametrize(
    ("matrix", "matrix_convention", "expected_cell"),
    [
        (np.array([[0, 0], [1, 0]]), "causallearn", (1, 0)),
        (np.array([[0.0, 0.0], [-0.82, 0.0]]), "target_to_source", (1, 0)),
        (np.array([[0, 1], [0, 0]]), "olc", (0, 1)),
    ],
)
def test_fix_cycles_returns_only_edges_actually_removed(
    monkeypatch,
    matrix,
    matrix_convention,
    expected_cell,
):
    """环路修复必须返回真实置零成功的边，而不是仅记录 LLM 文本。"""

    class FakeLLM:
        extra_body = None

        def model_copy(self, *, update):
            self.extra_body = update["extra_body"]
            return self

        def with_structured_output(self, *args, **kwargs):
            return object()

    class FakeRunnable:
        def invoke(self, payload):
            return fix_cycles.CycleFixDecision(
                remove_edge=["A", "B"],
                reason="break cycle",
            )

    class FakePrompt:
        def __or__(self, other):
            return FakeRunnable()

    monkeypatch.setattr(
        fix_cycles.ChatPromptTemplate,
        "from_messages",
        lambda *args, **kwargs: FakePrompt(),
    )

    revised_matrix, removed_edges = fix_cycles.fix_cycles_with_llm(
        matrix,
        [["A", "B"]],
        ["A", "B"],
        FakeLLM(),
        {},
        matrix_convention=matrix_convention,
    )

    assert revised_matrix[expected_cell] == 0
    assert removed_edges == [("A", "B")]


@pytest.mark.parametrize(
    ("matrix", "decision_edge"),
    [
        (
            np.array(
                [
                    [0, 1, 0, 1],
                    [0, 0, 1, 0],
                    [1, 0, 0, 0],
                    [0, 0, 0, 0],
                ]
            ),
            ["A", "D"],
        ),
        (np.array([[0, 2], [2, 0]]), ["A", "B"]),
    ],
)
def test_fix_cycles_rejects_edges_outside_cycle_or_not_directed(
    monkeypatch,
    matrix,
    decision_edge,
):
    """LLM 只能删除当前环路内且矩阵值明确为 1 的有向边。"""

    class FakeLLM:
        extra_body = None

        def model_copy(self, *, update):
            self.extra_body = update["extra_body"]
            return self

        def with_structured_output(self, *args, **kwargs):
            return object()

    class FakeRunnable:
        def invoke(self, payload):
            return fix_cycles.CycleFixDecision(
                remove_edge=decision_edge,
                reason="invalid choice",
            )

    class FakePrompt:
        def __or__(self, other):
            return FakeRunnable()

    monkeypatch.setattr(
        fix_cycles.ChatPromptTemplate,
        "from_messages",
        lambda *args, **kwargs: FakePrompt(),
    )

    original = matrix.copy()
    revised_matrix, removed_edges = fix_cycles.fix_cycles_with_llm(
        matrix,
        [["A", "B", "C"]] if len(matrix) == 4 else [["A", "B"]],
        ["A", "B", "C", "D"] if len(matrix) == 4 else ["A", "B"],
        FakeLLM(),
        {},
        matrix_convention="olc",
    )

    assert np.array_equal(revised_matrix, original)
    assert removed_edges == []


def _analysis_state(matrix, *, olc=False, matrix_convention=None):
    """构造包含真实展示边和指定矩阵约定的后处理状态。"""
    raw_results = {"adjacency_matrix": matrix.tolist()}
    if olc:
        raw_results["coefficient_matrix"] = np.zeros_like(matrix).tolist()
    analysis_result = {
        "success": True,
        "data": {"nodes": GRAPH_NODES, "edges": GRAPH_EDGES},
        "raw_results": raw_results,
    }
    if matrix_convention:
        analysis_result["matrix_convention"] = matrix_convention
    return {
        "causal_analysis_result": {
            **analysis_result,
        },
        "analysis_parameters": {},
        "knowledge_base_result": {},
    }


def _keep_all(edges, *_args):
    """模拟 LLM 对剩余候选边全部执行 keep。"""
    return {
        "schema_version": "edge_evaluation_v2",
        "decisions": [
            {
                "source": edge["source"],
                "target": edge["target"],
                "action": "keep",
            }
            for edge in edges
        ],
        "revised_edges": list(edges),
        "revision_summary": "kept remaining edges",
        "confidence": "high",
    }


def test_postprocess_preserves_direct_lingam_weight(monkeypatch):
    """DirectLiNGAM 保留边的机器可读权重必须进入最终修订图。"""
    state = {
        "causal_analysis_result": {
            "success": True,
            "algorithm": "direct_lingam",
            "matrix_convention": "target_to_source",
            "data": {
                "nodes": [{"id": "A", "label": "A"}, {"id": "B", "label": "B"}],
                "edges": [
                    {
                        "from": "A",
                        "to": "B",
                        "arrows": "to",
                        "label": "-0.82",
                        "weight": -0.82,
                    }
                ],
            },
            "raw_results": {
                "adjacency_matrix": [[0.0, 0.0], [-0.82, 0.0]],
            },
        },
        "analysis_parameters": {},
        "knowledge_base_result": {},
    }
    monkeypatch.setattr(nodes, "evaluate_edges_with_llm", _keep_all)

    result = asyncio.run(nodes.postprocess_node(state, object()))

    revised_edges = result["postprocess_result"]["revised_graph"]["edges"]
    assert revised_edges == [
        {
            "from": "A",
            "to": "B",
            "arrows": "to",
            "dashes": False,
            "label": "-0.82",
            "weight": -0.82,
        }
    ]


@pytest.mark.parametrize(
    ("matrix", "olc", "expected_convention", "removed_cell"),
    [
        (PC_CYCLE_MATRIX, False, "causallearn", (0, 2)),
        (DIRECT_LINGAM_CYCLE_MATRIX, False, "target_to_source", (0, 2)),
        (OLC_CYCLE_MATRIX, True, "olc", (2, 0)),
    ],
)
def test_postprocess_revised_graph_excludes_cycle_removed_edge(
    monkeypatch,
    matrix,
    olc,
    expected_convention,
    removed_cell,
):
    """环路删除必须先于边评估，并最终进入 vis-network 修订图。"""
    observed = {}

    def fake_fix(working_matrix, cycles, node_names, llm, state, *, matrix_convention):
        observed["matrix_convention"] = matrix_convention
        revised = working_matrix.copy()
        revised[removed_cell] = 0
        return revised, [("C", "A")]

    def fake_evaluate(edges, *args):
        observed["evaluated_edges"] = [
            (edge["source"], edge["target"]) for edge in edges
        ]
        return _keep_all(edges)

    monkeypatch.setattr(nodes, "fix_cycles_with_llm", fake_fix)
    monkeypatch.setattr(nodes, "evaluate_edges_with_llm", fake_evaluate)

    result = asyncio.run(
        nodes.postprocess_node(
            _analysis_state(
                matrix,
                olc=olc,
                matrix_convention=expected_convention,
            ),
            object(),
        )
    )

    revised = result["postprocess_result"]["revised_graph"]
    assert observed["matrix_convention"] == expected_convention
    assert observed["evaluated_edges"] == [("A", "B"), ("B", "C")]
    assert revised["nodes"] == GRAPH_NODES
    assert {(edge["from"], edge["to"]) for edge in revised["edges"]} == {
        ("A", "B"),
        ("B", "C"),
    }
    assert result["postprocess_result"]["edge_evaluation"]["cycle_removed_edges"] == [
        {"source": "C", "target": "A"}
    ]


def test_postprocess_marks_invalid_candidate_edges_as_error(monkeypatch):
    """非空候选边无法完整规范化时必须失败，避免输出静默丢边的修订图。"""
    state = {
        "causal_analysis_result": {
            "success": True,
            "data": {
                "nodes": [{"id": "A"}, {"id": "B"}],
                "edges": [
                    {"from": "A", "to": "B", "arrows": "to"},
                    {"from": "A", "arrows": "to"},
                ],
            },
            "raw_results": {"adjacency_matrix": [[0, 0], [0, 0]]},
        },
        "analysis_parameters": {},
        "knowledge_base_result": {},
    }
    monkeypatch.setattr(
        nodes,
        "evaluate_edges_with_llm",
        lambda *args: pytest.fail("结构非法时不应调用 LLM 边评估"),
    )

    result = asyncio.run(nodes.postprocess_node(state, object()))

    assert "error" in result["postprocess_result"]
    assert "revised_graph" not in result["postprocess_result"]


def test_edge_decisions_keep_reverse_remove_and_uncertain():
    """四种边决策都生成可追溯且保守的修订集合。"""
    candidates = [
        {"id": "1", "source": "A", "target": "B", "edge_type": "directed"},
        {
            "id": "2",
            "source": "B",
            "target": "C",
            "edge_type": "directed",
            "label": "B --> C",
        },
        {"id": "3", "source": "C", "target": "D", "edge_type": "directed"},
        {"id": "4", "source": "D", "target": "E", "edge_type": "undirected"},
    ]
    evaluation = EdgeEvaluationResult.model_validate(
        {
            "decisions": [
                {"source": "A", "target": "B", "action": "keep", "reason": "ok", "confidence": "high"},
                {
                    "source": "B",
                    "target": "C",
                    "action": "reverse",
                    "revised_source": "MALICIOUS",
                    "revised_target": "UNKNOWN",
                    "reason": "time",
                    "confidence": "high",
                },
                {"source": "C", "target": "D", "action": "remove", "reason": "bad", "confidence": "medium"},
                {"source": "D", "target": "E", "action": "uncertain", "reason": "weak", "confidence": "low"},
            ],
            "summary": "four actions",
            "confidence": "medium",
        }
    )

    result = _apply_edge_decisions(candidates, evaluation)

    assert [
        (edge["source"], edge["target"], edge["edge_type"])
        for edge in result["revised_edges"]
    ] == [
        ("A", "B", "directed"),
        ("C", "B", "directed"),
        ("D", "E", "undirected"),
    ]
    assert result["revised_edges"][1]["label"] == ""


def test_edge_evaluation_structured_failure_keeps_all_original_edges(monkeypatch):
    """边评估 Schema 失败时保留全部原边并标记低置信度。"""
    candidates = [
        {"id": "1", "source": "A", "target": "B", "edge_type": "directed"},
        {"id": "2", "source": "B", "target": "C", "edge_type": "directed"},
    ]

    def fail_structured(**kwargs):
        raise StructuredOutputError(
            node_name="postprocess_edge_evaluation",
            schema_name="EdgeEvaluationResult",
            cause=ValueError("approximate edges payload rejected"),
        )

    monkeypatch.setattr(evaluate_edge_llm, "invoke_structured", fail_structured)
    result = evaluate_edge_llm.evaluate_edges_with_llm(candidates, {}, object())

    assert result["confidence"] == "low"
    assert [(edge["source"], edge["target"]) for edge in result["revised_edges"]] == [
        ("A", "B"),
        ("B", "C"),
    ]
    assert all(decision["action"] == "keep" for decision in result["decisions"])
