"""报告提示词投影：剔除图分数但不改原事实。"""

from Agent.causal_agent.nodes import _prompt_analysis_facts


def test_prompt_analysis_facts_drops_scores_without_mutation() -> None:
    facts = {
        "data_metadata": {"rows": 10},
        "results": [
            {
                "algorithm": "causal.cdfm",
                "graph": {
                    "nodes": ["X0", "X1"],
                    "edges": [{"source": "X0", "target": "X1"}],
                    "graph_semantics": "dag",
                    "edge_score_matrix": [[0.0, 0.9], [0.1, 0.0]],
                    "score_type": "probability",
                },
            },
            {"algorithm": "causal.pc", "graph": None},
        ],
    }

    projected = _prompt_analysis_facts(facts)

    graph = projected["results"][0]["graph"]
    assert "edge_score_matrix" not in graph
    assert "score_type" not in graph
    assert graph["nodes"] == ["X0", "X1"]
    assert graph["edges"] == [{"source": "X0", "target": "X1"}]
    assert graph["graph_semantics"] == "dag"
    assert projected["data_metadata"] == {"rows": 10}
    assert projected["results"][1]["algorithm"] == "causal.pc"
    assert projected is not facts
    assert facts["results"][0]["graph"]["edge_score_matrix"] == [[0.0, 0.9], [0.1, 0.0]]
    assert facts["results"][0]["graph"]["score_type"] == "probability"
