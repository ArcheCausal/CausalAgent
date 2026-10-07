"""分析上下文投影、匹配与冻结输入刷新的纯单元测试。"""

from __future__ import annotations

from Agent.causal_agent.analysis_context import (
    MAX_ALGORITHM_SUMMARY_CHARS,
    MAX_EVIDENCE_SNIPPET_CHARS,
    build_context_commit,
    build_context_index,
    context_facts_text,
    context_graph,
    index_prompt_view,
    match_context_hint,
    normalize_algorithm_summary,
    normalize_evidence_items,
)
from Agent.deep_agent.context import FrozenInputRef, TrustedJobIdentity
from Agent.deep_agent_tools.models import (
    AlgorithmResult,
    AlgorithmResultProvenance,
    Diagnostics,
    EvidenceResult,
    FinalAnalysisDecision,
    GraphEdge,
    ResultAssessment,
    SafeWarning,
    StandardizedGraph,
    WebEvidenceResult,
)


JOB_ID = "11111111-1111-4111-8111-111111111111"
SESSION_ID = "22222222-2222-4222-8222-222222222222"


def _provenance(**overrides):
    payload = dict(
        job_id=JOB_ID,
        attempt_count=1,
        lease_epoch=2,
        input_identity="hash-a",
        spec_digest="spec-digest",
        invocation_id="invocation-1",
        capability_id="causal_pc",
        capability_version="1.0",
        algorithm_runner_version="runner-3",
    )
    payload.update(overrides)
    return AlgorithmResultProvenance(**payload)


def _result(**overrides):
    payload = dict(
        result_ref="invocation-1:0",
        invocation_id="invocation-1",
        provider_call_id="call-1",
        capability_id="causal_pc",
        capability_version="1.0",
        status="valid",
        standardized_graph=StandardizedGraph(
            graph_semantics="source -> target",
            nodes=["promotion", "sales"],
            edges=[
                GraphEdge(
                    source="promotion",
                    target="sales",
                    edge_type="directed",
                    weight=0.42,
                )
            ],
        ),
        graph_semantics="source -> target",
        summary="促销活动到销售额存在候选因果边。",
        diagnostics=Diagnostics(
            sample_count=120,
            variable_count=2,
            assumptions_checked=["linearity", "no_latent_confounder"],
        ),
        warnings=[SafeWarning(code="MCP_LOW_SAMPLE", message="样本量偏小")],
        provenance=_provenance(),
    )
    payload.update(overrides)
    return AlgorithmResult(**payload)


def _context_row(**overrides):
    payload = {
        "analysis_context_id": "ctx-1",
        "session_id": SESSION_ID,
        "user_id": 7,
        "status": "active",
        "filename": "sales.csv",
        "target": "销售额",
        "treatment": "促销活动",
        "analysis_question": "促销活动对销售额的影响",
        "latest_report_title": "促销效果报告",
        "updated_at": "2026-09-20T10:00:00",
        "latest_algorithm_summary": {
            "algorithm": "causal_pc",
            "status": "valid",
            "summary": "促销活动到销售额存在候选因果边。",
            "parameters": {"target": "销售额", "treatment": "促销活动"},
            "graph": {
                "nodes": ["promotion", "sales"],
                "edges": [
                    {
                        "source": "promotion",
                        "target": "sales",
                        "edge_type": "directed",
                        "weight": 0.42,
                    }
                ],
            },
        },
    }
    payload.update(overrides)
    return payload


def test_algorithm_summary_keeps_normalized_graph_diagnostics_and_warnings():
    summary = normalize_algorithm_summary(
        _result(),
        parameters={"target": "sales", "treatment": "promotion"},
    )

    assert summary["result_ref"] == "invocation-1:0"
    assert summary["algorithm"] == "causal_pc"
    assert summary["status"] == "valid"
    assert summary["parameters"] == {"target": "sales", "treatment": "promotion"}
    assert summary["graph_semantics"] == "source -> target"
    assert summary["graph"]["nodes"] == ["promotion", "sales"]
    assert summary["graph"]["edges"] == [
        {
            "source": "promotion",
            "target": "sales",
            "edge_type": "directed",
            "weight": 0.42,
        }
    ]
    assert summary["assumptions"] == ["linearity", "no_latent_confounder"]
    assert summary["warnings"][0]["message"] == "样本量偏小"
    assert summary["diagnostics"]["sample_count"] == 120
    assert summary["provenance"]["spec_digest"] == "spec-digest"
    assert summary["provenance"]["algorithm_runner_version"] == "runner-3"


def test_algorithm_summary_clips_long_text_and_keeps_failure_code():
    failed = _result(
        status="execution_failed",
        standardized_graph=None,
        graph_semantics=None,
        summary="x" * 5000,
        diagnostics=Diagnostics(safe_error_code="ALGORITHM_EXECUTION_FAILED"),
    )

    summary = normalize_algorithm_summary(failed)

    assert len(summary["summary"]) == MAX_ALGORITHM_SUMMARY_CHARS
    assert summary["graph"] is None
    assert summary["diagnostics"]["safe_error_code"] == "ALGORITHM_EXECUTION_FAILED"


def test_evidence_projection_keeps_ref_snippet_locator_and_release():
    rag = EvidenceResult(
        evidence_ref="ev-1",
        snippet="s" * 900,
        source_title="内部文档",
        source_url=None,
        locator="第 3 页",
        release_id="release-9",
        score=0.5,
    )

    items = normalize_evidence_items({"ev-1": rag})

    assert len(items) == 1
    assert items[0]["evidence_ref"] == "ev-1"
    assert len(items[0]["snippet"]) == MAX_EVIDENCE_SNIPPET_CHARS
    assert items[0]["locator"] == "第 3 页"
    assert items[0]["release_id"] == "release-9"
    assert items[0]["source_url"] is None


def test_web_evidence_keeps_title_and_url():
    web = WebEvidenceResult(
        evidence_ref="ev-web",
        snippet="网页摘要",
        source_title="网页标题",
        source_url="https://example.test/page",
        locator="SearXNG",
        score=0.7,
        provider_status="ok",
        fetched_at="2026-09-20T10:00:00+00:00",
    )

    items = normalize_evidence_items({"ev-web": web})

    assert items[0]["source_title"] == "网页标题"
    assert items[0]["source_url"] == "https://example.test/page"


def test_evidence_projection_drops_mismatched_or_invalid_entries():
    rag = EvidenceResult(evidence_ref="ev-1", snippet="内容")

    assert normalize_evidence_items({"other-key": rag}) == []
    assert normalize_evidence_items({"ev-1": {"evidence_ref": "ev-1"}}) == []


def test_context_index_keeps_descriptive_fields_and_prompt_view_hides_ids():
    index = build_context_index([_context_row()])

    assert index[0]["analysis_context_id"] == "ctx-1"
    assert index[0]["target"] == "销售额"
    assert index[0]["latest_report_title"] == "促销效果报告"
    assert index[0]["short_summary"] == "促销活动到销售额存在候选因果边。"

    view = index_prompt_view(index)

    assert "ctx-1" not in view
    assert "sales.csv" in view
    assert "历史分析 1" in view


def test_match_context_hint_supports_filename_target_ordinal_and_ambiguity():
    index = build_context_index(
        [
            _context_row(),
            _context_row(
                analysis_context_id="ctx-2",
                target="客户流失",
                analysis_question="促销活动对客户流失的影响",
                latest_report_title="流失分析报告",
                latest_algorithm_summary={
                    "summary": "促销活动与客户流失相关",
                    "parameters": {"target": "客户流失", "treatment": "促销活动"},
                },
            ),
        ]
    )

    assert [
        entry["analysis_context_id"] for entry in match_context_hint("客户流失", index)
    ] == ["ctx-2"]
    assert [
        entry["analysis_context_id"] for entry in match_context_hint("销售额", index)
    ] == ["ctx-1"]
    assert [
        entry["analysis_context_id"] for entry in match_context_hint("sales.csv", index)
    ] == ["ctx-1", "ctx-2"]
    assert [
        entry["analysis_context_id"] for entry in match_context_hint("第二个", index)
    ] == ["ctx-2"]
    assert [
        entry["analysis_context_id"] for entry in match_context_hint("第1个", index)
    ] == ["ctx-1"]
    assert match_context_hint("不存在的主题", index) == []
    assert match_context_hint(None, index) == []


def test_context_graph_and_facts_text_use_confirmed_facts_only():
    context = _context_row()
    context["latest_rag_evidence"] = [
        {
            "evidence_ref": "ev-1",
            "snippet": "证据摘要",
            "source_title": "内部文档",
            "source_url": None,
            "locator": "第 3 页",
            "release_id": "release-9",
            "score": 0.5,
        }
    ]

    assert context_graph(context) == {
        "nodes": ["promotion", "sales"],
        "edges": [
            {
                "source": "promotion",
                "target": "sales",
                "edge_type": "directed",
                "weight": 0.42,
            }
        ],
        "graph_semantics": None,
    }

    text = context_facts_text(context)

    assert "促销活动" in text
    assert "promotion -> sales" in text
    assert "内部文档" in text
    assert "促销效果报告" in text


def test_context_graph_requires_nodes_and_an_edge_list():
    assert context_graph({}) is None
    assert (
        context_graph(
            {"latest_algorithm_summary": {"graph": {"nodes": ["a"], "edges": []}}}
        )
        == {"nodes": ["a"], "edges": [], "graph_semantics": None}
    )
    assert (
        context_graph(
            {"latest_algorithm_summary": {"graph": {"nodes": [], "edges": []}}}
        )
        is None
    )
    assert (
        context_graph(
            {"latest_algorithm_summary": {"graph": {"nodes": ["a"], "edges": "no"}}}
        )
        is None
    )


def test_build_context_commit_projects_primary_result_evidence_and_report_id():
    state = {
        "analysis_context": {"analysis_context_id": "ctx-1"},
        "analysis_parameters": {"target": "sales", "treatment": "promotion"},
        "deep_agent_algorithm_results": {"invocation-1:0": _result()},
        "deep_agent_decision": FinalAnalysisDecision(
            outcome="algorithm_supported",
            primary_result_ref="invocation-1:0",
            result_assessments=[
                ResultAssessment(
                    result_ref="invocation-1:0",
                    disposition="primary",
                    rationale="唯一有效结果",
                )
            ],
            conflict_status="none",
            selection_rationale="只有唯一有效结果",
            confidence="medium",
        ),
        "deep_agent_rag_evidence": {
            "ev-1": EvidenceResult(evidence_ref="ev-1", snippet="证据摘要")
        },
        "report_document": {"report_id": "report-1"},
    }

    commit = build_context_commit(state)

    assert commit["analysis_context_id"] == "ctx-1"
    assert commit["algorithm_summary"]["result_ref"] == "invocation-1:0"
    assert commit["rag_evidence"][0]["evidence_ref"] == "ev-1"
    assert commit["web_evidence"] is None
    assert commit["report_id"] == "report-1"


def test_build_context_commit_returns_none_without_confirmed_facts():
    assert build_context_commit({"analysis_context": {"analysis_context_id": "ctx-1"}}) is None


def test_build_context_commit_skips_ambiguous_valid_results():
    state = {
        "deep_agent_algorithm_results": {
            "invocation-1:0": _result(),
            "invocation-2:0": _result(
                result_ref="invocation-2:0",
                invocation_id="invocation-2",
                provider_call_id="call-2",
                provenance=_provenance(invocation_id="invocation-2"),
            ),
        },
        "deep_agent_decision": None,
    }

    assert build_context_commit(state) is None


def test_frozen_input_refresh_updates_mcp_context_and_identity():
    frozen = FrozenInputRef(
        user_file_id=1,
        object_id=2,
        content_hash="hash-a",
        filename="a.csv",
    )
    identity = TrustedJobIdentity(
        job_id=JOB_ID,
        session_id=SESSION_ID,
        user_id=7,
        attempt_count=1,
        lease_epoch=2,
        worker_id="worker-1",
        input_identity="hash-a",
        input_snapshot_digest="hash-a",
        frozen_input=frozen,
    )

    assert identity.current_input_identity() == "hash-a"
    invocation_id = "33333333-3333-4333-8333-333333333333"

    assert (
        identity.to_mcp_context(invocation_id=invocation_id).input_snapshot_digest
        == "hash-a"
    )

    assert (
        frozen.apply_snapshot(
            user_file_id=3,
            object_id=4,
            content_hash="hash-b",
            filename="b.csv",
        )
        is True
    )

    assert identity.current_input_identity() == "hash-b"
    assert (
        identity.to_mcp_context(invocation_id=invocation_id).input_snapshot_digest
        == "hash-b"
    )

    assert (
        frozen.apply_snapshot(
            user_file_id=3,
            object_id=4,
            content_hash="hash-b",
            filename="b.csv",
        )
        is False
    )
    assert frozen.revision == 1
