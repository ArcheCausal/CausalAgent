"""P2-U Deep Agent State 与父图投影的协议测试。"""

from __future__ import annotations

import json

import pytest

from Agent.deep_agent.state import (
    assert_checkpoint_state_safe,
    from_deep_agent_output,
    initial_deep_agent_state,
    to_deep_agent_input,
)
from Agent.deep_agent_tools.models import DataProfile
from Agent.deep_agent.prompts import (
    MANDATORY_ALGORITHM_INSTRUCTION,
    MANDATORY_WEB_INSTRUCTION,
)


ANALYSIS_PARENT = {
    "messages": [],
    "analysis_question": "question",
    "analysis_parameters": {"target": "y"},
    "file_summary": {"rows": 10, "columns": ["x", "y"]},
}


def test_analysis_route_requires_algorithm_but_leaves_rag_optional() -> None:
    """分析运行强制算法调用，但知识库检索仍由 Agent 自主判断。"""

    projected = to_deep_agent_input({**ANALYSIS_PARENT, "route_decision": "fold"})

    assert MANDATORY_ALGORITHM_INSTRUCTION in [
        message["content"] for message in projected["messages"]
    ]
    assert all(
        "必须至少调用一次 rag_evidence_search" not in message["content"]
        for message in projected["messages"]
    )


def test_non_analysis_route_has_no_algorithm_requirement() -> None:
    """非分析路由不会带着“必须调用算法”的约束启动 Deep Agent。"""

    projected = to_deep_agent_input(
        {**ANALYSIS_PARENT, "route_decision": "inquiry_answer"}
    )

    assert MANDATORY_ALGORITHM_INSTRUCTION not in [
        message["content"] for message in projected["messages"]
    ]


def test_enabled_web_search_requires_a_web_evidence_call() -> None:
    """开启联网搜索的分析运行必须把 Web 检索合同注入 Deep Agent。"""

    projected = to_deep_agent_input(
        {**ANALYSIS_PARENT, "route_decision": "fold", "web_search_enabled": True}
    )

    assert MANDATORY_WEB_INSTRUCTION in [
        message["content"] for message in projected["messages"]
    ]


def test_disabled_web_search_has_no_web_requirement() -> None:
    """关闭联网搜索时不向模型注入必须联网的约束。"""

    projected = to_deep_agent_input(
        {**ANALYSIS_PARENT, "route_decision": "fold", "web_search_enabled": False}
    )

    assert MANDATORY_WEB_INSTRUCTION not in [
        message["content"] for message in projected["messages"]
    ]


def test_state_always_has_empty_ledger_and_independent_reducers() -> None:
    state = initial_deep_agent_state(
        data_profile=DataProfile(row_count=10, column_count=2, column_names=("x", "y")),
        analysis_question="compare methods",
    )
    assert state["action_ledger"] == {}
    assert state["algorithm_results"] == {}
    assert state["rag_evidence"] == {}
    assert state["web_evidence"] == {}
    assert state["finalization_retry_count"] == 0


def test_parent_projection_does_not_copy_job_or_runtime_fields() -> None:
    parent = {
        "messages": ["message"],
        "analysis_question": "question",
        "analysis_parameters": {"target": "y"},
        "file_summary": {"rows": 10, "columns": ["x", "y"]},
        "job_id": "job-secret-to-parent-only",
        "execution_guard": object(),
    }
    projected = to_deep_agent_input(parent)
    assert projected["analysis_question"] == "question"
    assert projected["data_profile"].column_names == ("x", "y")
    assert "job_id" not in projected
    assert "execution_guard" not in projected

    update = from_deep_agent_output(projected)
    assert set(update) == {
        "deep_agent_algorithm_results",
        "deep_agent_action_ledger",
        "deep_agent_rag_evidence",
        "deep_agent_web_evidence",
        "deep_agent_decision",
        "deep_agent_structured_response",
        "deep_agent_status",
    }


def test_runtime_only_state_keys_are_rejected() -> None:
    with pytest.raises(TypeError, match="runtime-only"):
        assert_checkpoint_state_safe({"algorithm_executor": object()})


def test_linearity_signal_and_preprocess_report_reach_the_algorithm_prompt() -> None:
    """选算法的模型必须同时看得见 nonlinearity 数值和预处理报告。

    两者都只经由那条 system message 抵达模型：子图 state 里既不存
    ``preprocess_summary``，父图的其它字段也不会被投影。所以报告必须显式写进
    JSON，光放父图 state 里是看不见的。
    """

    nonlinearity = {
        "ratio": 3.19,
        "verdict": "nonlinear",
        "n_vars": 5,
        "n_rows": 1000,
        "sampled": False,
        "reason": None,
    }
    projected = to_deep_agent_input(
        {
            **ANALYSIS_PARENT,
            "route_decision": "fold",
            "preprocess_summary": "非线性度 ratio 3.19，以非线性机制为主。",
            "analysis_parameters": {"target": "y", "nonlinearity": nonlinearity},
        }
    )

    payload = json.loads(projected["messages"][0]["content"])
    assert payload["preprocess_summary"].startswith("非线性度")
    assert payload["analysis_parameters"]["nonlinearity"] == nonlinearity
    assert "nonlinearity.ratio" in MANDATORY_ALGORITHM_INSTRUCTION

