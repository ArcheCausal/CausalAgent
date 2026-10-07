"""context_switch 节点解析与切换语义的单元测试。"""

from __future__ import annotations

import asyncio
import os
import sys
import types

from langchain_core.messages import HumanMessage


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
    """隔离路由测试不需要的数据库依赖。"""
    agent_connect = types.ModuleType("Database.agent_connect")
    agent_connect.require_frozen_file_for_job = lambda *args, **kwargs: None
    sys.modules.setdefault("Database.agent_connect", agent_connect)


_install_import_stubs()

from Agent.causal_agent import nodes
from Agent.deep_agent.context import AgentRunContext, FrozenInputRef, TrustedJobIdentity


JOB_ID = "11111111-1111-4111-8111-111111111111"
SESSION_ID = "22222222-2222-4222-8222-222222222222"

TARGET_INDEX_ENTRY = {
    "analysis_context_id": "ctx-target",
    "filename": "sales.csv",
    "target": "销售额",
    "treatment": "促销活动",
    "latest_report_title": "促销效果报告",
    "short_summary": "促销活动与销售额相关",
    "updated_at": "2026-09-20T10:00:00",
}


def _identity(*, content_hash="hash-a", filename="data.csv"):
    frozen = FrozenInputRef(
        user_file_id=11,
        object_id=22,
        content_hash=content_hash,
        filename=filename,
    )
    identity = TrustedJobIdentity(
        job_id=JOB_ID,
        session_id=SESSION_ID,
        user_id=7,
        attempt_count=1,
        lease_epoch=3,
        worker_id="worker-1",
        input_identity=content_hash,
        input_snapshot_digest=content_hash,
        frozen_input=frozen,
    )
    return identity, frozen


def _runtime(identity):
    return types.SimpleNamespace(
        context=AgentRunContext(execution_guard=None, trusted_identity=identity)
    )


def _state(**updates):
    state = {
        "messages": [HumanMessage(content="回到 sales.csv 的分析")],
        "user_id": 7,
        "username": "tester",
        "session_id": SESSION_ID,
        "job_id": JOB_ID,
        "agent_decision": {
            "intent": "switch_analysis_context",
            "context_hint": "sales.csv",
        },
        "analysis_context": {
            "analysis_context_id": "ctx-current",
            "filename": "data.csv",
            "target": "访问量",
            "latest_algorithm_summary": {"summary": "旧结论"},
        },
        "analysis_context_index": [dict(TARGET_INDEX_ENTRY)],
        "analysis_parameters": {"target": "访问量"},
        "causal_analysis_result": {"success": True, "data": {"nodes": ["a"], "edges": []}},
        "chart_assets": {"chart_1": {"asset_key": "chart_1"}},
        "deep_agent_algorithm_results": {"invocation-1:0": {"result_ref": "invocation-1:0"}},
    }
    state.update(updates)
    return state


def _switch_result(**overrides):
    payload = {
        "status": "switched",
        "context": {
            "analysis_context_id": "ctx-target",
            "filename": "sales.csv",
            "target": "销售额",
            "treatment": "促销活动",
            "latest_report_title": "促销效果报告",
            "latest_algorithm_summary": {"summary": "促销活动与销售额相关"},
        },
        "file_snapshot": {
            "input_user_file_id": 31,
            "input_object_id": 32,
            "input_file_hash": "hash-b",
            "input_filename": "sales.csv",
        },
        "file_changed": True,
    }
    payload.update(overrides)
    return payload


def test_unique_match_switches_context_and_drops_previous_facts(monkeypatch):
    """唯一命中时切换上下文，并且不继承上一个上下文的算法产物。"""
    calls = {}

    def fake_switch(**kwargs):
        calls.update(kwargs)
        return _switch_result()

    monkeypatch.setattr(nodes, "switch_to_context", fake_switch)
    identity, frozen = _identity()
    result = asyncio.run(
        nodes.context_switch_node(_state(), runtime=_runtime(identity))
    )

    assert calls["context_id"] == "ctx-target"
    assert calls["job_id"] == JOB_ID
    assert calls["user_id"] == 7
    assert calls["worker_id"] == "worker-1"
    assert result["route_decision"] == "inquiry_answer"
    assert result["analysis_context"]["analysis_context_id"] == "ctx-target"
    assert result["analysis_parameters"] is None
    assert result["causal_analysis_result"] is None
    assert result["chart_assets"] is None
    assert result["deep_agent_algorithm_results"] == {}
    assert result["file_summary"]["filename"] == "sales.csv"
    assert result["file_summary"]["user_file_id"] == 31
    assert result["analysis_context_index"][0]["analysis_context_id"] == "ctx-current"
    assert frozen.content_hash == "hash-b"
    assert identity.current_input_identity() == "hash-b"
    assert result["context_resolution"]["status"] == "switched"
    assert result["context_resolution"]["file_changed"] is True


def test_switch_to_context_without_facts_routes_to_fold(monkeypatch):
    """目标上下文还没有结论时，切换后直接进入分析流程。"""
    monkeypatch.setattr(
        nodes,
        "switch_to_context",
        lambda **kwargs: _switch_result(
            context={"analysis_context_id": "ctx-target", "filename": "sales.csv"},
            file_changed=False,
        ),
    )
    identity, _frozen = _identity()
    result = asyncio.run(
        nodes.context_switch_node(_state(), runtime=_runtime(identity))
    )

    assert result["route_decision"] == "fold"
    assert result["context_resolution"]["followup_route"] == "fold"


def test_rerun_analysis_re_runs_after_switching(monkeypatch):
    """重新分析在解析上下文之后仍然进入分析流程。"""
    monkeypatch.setattr(nodes, "switch_to_context", lambda **kwargs: _switch_result())
    identity, _frozen = _identity()
    state = _state(
        agent_decision={"intent": "rerun_analysis", "context_hint": "sales.csv"}
    )
    result = asyncio.run(nodes.context_switch_node(state, runtime=_runtime(identity)))

    assert result["route_decision"] == "fold"


def test_rerun_analysis_without_hint_targets_current_context(monkeypatch):
    """没有点名其他分析时，重新分析的目标就是当前上下文。"""
    calls = {}

    def fake_switch(**kwargs):
        calls.update(kwargs)
        return _switch_result(file_changed=False)

    monkeypatch.setattr(nodes, "switch_to_context", fake_switch)
    identity, _frozen = _identity()
    state = _state(agent_decision={"intent": "rerun_analysis", "context_hint": None})
    result = asyncio.run(nodes.context_switch_node(state, runtime=_runtime(identity)))

    assert calls["context_id"] == "ctx-current"
    assert result["route_decision"] == "fold"


def test_ambiguous_candidates_return_clarification_without_switching(monkeypatch):
    """同名文件下多个分析必须澄清，并且不修改 active 上下文。"""
    def explode(**kwargs):
        raise AssertionError("歧义时不允许切换上下文")

    monkeypatch.setattr(nodes, "switch_to_context", explode)
    identity, _frozen = _identity()
    state = _state(
        analysis_context_index=[
            dict(TARGET_INDEX_ENTRY),
            dict(
                TARGET_INDEX_ENTRY,
                analysis_context_id="ctx-other",
                target="客户流失",
                latest_report_title="流失分析报告",
            ),
        ]
    )
    result = asyncio.run(nodes.context_switch_node(state, runtime=_runtime(identity)))

    assert result["route_decision"] == "inquiry_answer"
    assert result["context_resolution"]["status"] == "ambiguous_context"
    assert result["context_resolution"]["candidate_count"] == 2
    assert "ctx-target" not in result["context_resolution"]["clarification_question"]
    assert "目标变量" in result["context_resolution"]["clarification_question"]


def test_missing_context_file_returns_clarification(monkeypatch):
    """目标上下文的文件已经被删除时不能切换。"""
    monkeypatch.setattr(
        nodes,
        "switch_to_context",
        lambda **kwargs: {"status": "file_missing"},
    )
    identity, _frozen = _identity()
    result = asyncio.run(
        nodes.context_switch_node(_state(), runtime=_runtime(identity))
    )

    assert result["route_decision"] == "inquiry_answer"
    assert result["context_resolution"]["status"] == "file_missing"
    assert "文件" in result["context_resolution"]["clarification_question"]


def test_unknown_hint_returns_clarification(monkeypatch):
    """没有任何候选时提示用户补充说明。"""
    def explode(**kwargs):
        raise AssertionError("无匹配时不允许切换上下文")

    monkeypatch.setattr(nodes, "switch_to_context", explode)
    identity, _frozen = _identity()
    state = _state(
        agent_decision={"intent": "switch_analysis_context", "context_hint": "另一个主题"}
    )
    result = asyncio.run(nodes.context_switch_node(state, runtime=_runtime(identity)))

    assert result["route_decision"] == "inquiry_answer"
    assert result["context_resolution"]["status"] == "no_match"
    assert "历史分析" in result["context_resolution"]["clarification_question"]



def test_missing_library_file_returns_dedicated_clarification(monkeypatch):
    """点名了文件库里没有的文件时，提示用户先上传成功。"""
    monkeypatch.setattr(
        nodes,
        "find_user_file_by_name",
        lambda user_id, filename: [],
    )
    identity, _frozen = _identity()
    state = _state(
        agent_decision={"intent": "switch_analysis_context", "context_hint": "new.csv"},
        analysis_context_index=[],
    )
    result = asyncio.run(nodes.context_switch_node(state, runtime=_runtime(identity)))

    assert result["route_decision"] == "inquiry_answer"
    assert result["context_resolution"]["status"] == "file_not_in_library"
    assert "文件库" in result["context_resolution"]["clarification_question"]


def test_unmatched_filename_creates_context_for_authorized_file(monkeypatch):
    """点名了文件库里存在的其他文件时，为它建立新的上下文并重新冻结输入。"""
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return {
            "status": "created",
            "analysis_context_id": "ctx-new",
            "file_snapshot": {
                "input_user_file_id": 41,
                "input_object_id": 42,
                "input_file_hash": "hash-new",
                "input_filename": "new.csv",
            },
        }

    monkeypatch.setattr(
        nodes,
        "find_user_file_by_name",
        lambda user_id, filename: [{"user_file_id": 41, "filename": filename}],
    )
    monkeypatch.setattr(nodes, "create_context_for_new_file", fake_create)
    identity, frozen = _identity()
    state = _state(
        agent_decision={"intent": "switch_analysis_context", "context_hint": "new.csv"},
        analysis_context_index=[],
    )
    result = asyncio.run(nodes.context_switch_node(state, runtime=_runtime(identity)))

    assert captured["user_file_id"] == 41
    assert result["route_decision"] == "fold"
    assert result["analysis_context"]["analysis_context_id"] == "ctx-new"
    assert result["context_resolution"]["status"] == "created"
    assert frozen.content_hash == "hash-new"


def test_duplicate_library_filenames_return_clarification(monkeypatch):
    """文件库中同名文件有多个时不能自行选择。"""
    monkeypatch.setattr(
        nodes,
        "find_user_file_by_name",
        lambda user_id, filename: [
            {"user_file_id": 41, "filename": filename},
            {"user_file_id": 43, "filename": filename},
        ],
    )
    identity, _frozen = _identity()
    state = _state(
        agent_decision={"intent": "switch_analysis_context", "context_hint": "new.csv"},
        analysis_context_index=[],
    )
    result = asyncio.run(nodes.context_switch_node(state, runtime=_runtime(identity)))

    assert result["context_resolution"]["status"] == "ambiguous_file"
    assert result["context_resolution"]["candidate_count"] == 2


def test_missing_trusted_identity_returns_clarification(monkeypatch):
    """没有可信 Job 身份时不能访问数据库，只能回到澄清。"""
    def explode(**kwargs):
        raise AssertionError("没有可信身份时不允许写库")

    monkeypatch.setattr(nodes, "switch_to_context", explode)
    result = asyncio.run(
        nodes.context_switch_node(
            _state(),
            runtime=types.SimpleNamespace(context=None),
        )
    )

    assert result["route_decision"] == "inquiry_answer"
    assert result["context_resolution"]["status"] == "clarify"
