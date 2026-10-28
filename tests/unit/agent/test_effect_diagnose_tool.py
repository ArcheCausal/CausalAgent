"""effect_diagnose：确定性判定、终态 ledger 与 payload。"""

from __future__ import annotations

import asyncio
import json

import pytest
from langgraph.prebuilt import ToolRuntime

from Agent.deep_agent import AgentRunContext, TrustedJobIdentity
from Agent.deep_agent_tools.algorithm_specs import PC_SPEC
from Agent.deep_agent_tools.effect_diagnose_tool import (
    build_default_effect_diagnose_tool,
)
from Agent.deep_agent_tools.models import (
    AlgorithmResult,
    AlgorithmResultProvenance,
    Diagnostics,
    GraphEdge,
    StandardizedGraph,
)
from tests.support.bound_analysis_input import bind_analysis_input
from tests.support.local_algorithm_execution import PC_CSV


def _identity() -> TrustedJobIdentity:
    identity = TrustedJobIdentity(
        job_id="00000000-0000-0000-0000-000000000301",
        session_id="00000000-0000-0000-0000-000000000302",
        user_id=7,
        attempt_count=0,
        lease_epoch=1,
        worker_id="worker-1",
        input_identity="input-sha",
    )
    return bind_analysis_input(
        identity,
        PC_CSV,
        semantic_kinds={"x": "continuous", "y": "continuous"},
    )


def _graph_result(
    identity: TrustedJobIdentity,
    *,
    nodes=("x", "y"),
    edges=(("x", "y"),),
    edge_type="directed",
    status="valid",
    attempt_count=0,
) -> AlgorithmResult:
    graph = (
        StandardizedGraph(
            graph_semantics="dag_target_to_source",
            nodes=list(nodes),
            edges=[
                GraphEdge(source=source, target=target, edge_type=edge_type)
                for source, target in edges
            ],
        )
        if nodes
        else None
    )
    return AlgorithmResult(
        result_ref="graph-invocation:0",
        invocation_id="graph-invocation",
        provider_call_id="graph-call-1",
        capability_id=PC_SPEC.capability_id,
        capability_version=PC_SPEC.version,
        status=status,
        standardized_graph=graph,
        diagnostics=(
            Diagnostics()
            if status == "valid"
            else Diagnostics(safe_error_code="TEST_GRAPH_UNAVAILABLE")
        ),
        provenance=AlgorithmResultProvenance(
            job_id=str(identity.job_id),
            attempt_count=attempt_count,
            lease_epoch=identity.lease_epoch,
            input_identity=identity.current_input_identity(),
            source_input_identity=identity.source_input_identity(),
            analysis_dataset_id=identity.analysis_input.dataset_id,
            spec_digest=PC_SPEC.spec_digest,
        ),
    )


def _run(identity, result, *, treatment="x", outcome="y", ref="graph-invocation:0"):
    context = AgentRunContext(execution_guard=None, trusted_identity=identity)
    tool = build_default_effect_diagnose_tool().to_langchain_tool()
    command = asyncio.run(
        tool.coroutine(
            runtime=ToolRuntime(
                tool_call_id="diagnose-call-1",
                state={
                    "message_execution_id": "diagnose-response-1",
                    "algorithm_results": {result.result_ref: result},
                    "analysis_parameters": {"data_profile": {"row_count": 100}},
                },
                context=context,
                config={},
                stream_writer=[].append,
                store=None,
            ),
            graph_result_ref=ref,
            treatment=treatment,
            outcome=outcome,
        )
    )
    record = next(iter(command.update["action_ledger"].values()))
    payload = json.loads(command.update["messages"][0].content)
    return record, payload


def test_diagnose_proceeds_and_writes_subject_ref() -> None:
    identity = _identity()
    record, payload = _run(identity, _graph_result(identity))

    assert record.tool_name == "effect_diagnose"
    assert record.final_status == "succeeded"
    assert record.subject_ref == "graph-invocation:0"
    assert payload["verdict"] == "proceed"
    assert payload["checks"] == {
        "reference_valid": True,
        "variables_present": True,
        "directed_only": True,
        "acyclic": True,
        "treatment_is_ancestor": True,
    }
    assert payload["reasons"] == []
    assert payload["treatment_type"] == "continuous"
    assert payload["n_rows"] == 100
    assert payload["recommended_estimator"] == "causal.backdoor_ols"
    assert payload["graph_result_ref"] == "graph-invocation:0"


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"edges": (("x", "y"),), "edge_type": "undirected"}, "graph_not_directed"),
        ({"edges": (("x", "y"), ("y", "x"))}, "graph_has_cycle"),
        ({"edges": (("y", "x"),)}, "treatment_not_ancestor"),
        ({"nodes": ("x",), "edges": ()}, "variables_not_estimable"),
    ],
)
def test_diagnose_rejects_structural_problems(kwargs, code) -> None:
    identity = _identity()
    record, payload = _run(identity, _graph_result(identity, **kwargs))

    assert record.final_status == "not_applicable"
    assert record.subject_ref == "graph-invocation:0"
    assert payload["verdict"] == "reject"
    assert code in {reason["code"] for reason in payload["reasons"]}


def test_diagnose_rejects_same_treatment_and_outcome() -> None:
    identity = _identity()
    record, payload = _run(identity, _graph_result(identity), outcome="x")

    assert record.final_status == "not_applicable"
    assert payload["verdict"] == "reject"
    assert "variables_not_estimable" in {
        reason["code"] for reason in payload["reasons"]
    }


def test_diagnose_fails_for_missing_reference() -> None:
    identity = _identity()
    record, payload = _run(identity, _graph_result(identity), ref="missing:0")

    assert record.final_status == "failed"
    assert record.subject_ref == "missing:0"
    assert payload["verdict"] == "error"
    assert payload["reasons"][0]["code"] == "graph_result_missing"


def test_diagnose_fails_for_non_valid_result() -> None:
    identity = _identity()
    record, payload = _run(
        identity, _graph_result(identity, status="execution_failed")
    )

    assert record.final_status == "failed"
    assert payload["verdict"] == "error"
    assert payload["reasons"][0]["code"] == "reference_not_trusted"


def test_diagnose_fails_for_stale_provenance() -> None:
    identity = _identity()
    record, payload = _run(
        identity, _graph_result(identity, attempt_count=3)
    )

    assert record.final_status == "failed"
    assert payload["reasons"][0]["code"] == "reference_not_trusted"
