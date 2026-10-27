"""P2-U ToolStrategy 终态和 finalization retry 预算测试。"""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from datetime import datetime, timezone
import pytest
from langchain_core.messages import HumanMessage
from langgraph.prebuilt import ToolRuntime

from Agent.deep_agent import AgentRunContext, FinalizationGate, TrustedJobIdentity
from Agent.deep_agent.memory import build_in_memory_backend
from Agent.deep_agent_tools import build_default_registry
from Agent.deep_agent_tools.adapters import build_default_adapters
from Agent.deep_agent_tools.algorithm_tools import build_algorithm_tools
from tests.support.bound_analysis_input import bind_analysis_input
from tests.support.local_algorithm_execution import LocalAlgorithmExecutor, PC_CSV, PC_INSUFFICIENT_SAMPLE_CSV

from Agent.deep_agent.finalization import (
    FinalizationRetryController,
    StructuredResponseError,
    _requires_effect_estimation,
    build_finalization_retry_instruction,
    validate_decision_references,
    validate_structured_response,
)
from Agent.causal_agent.graph import (
    _emit_public_final_decision,
    _finalization_gate_node,
    _legacy_rag_evidence_result,
    _legacy_web_evidence_result,
)
from Agent.deep_agent_tools.identity import build_invocation_id, build_result_ref
from Agent.deep_agent_tools.models import (
    ActionAttempt,
    AlgorithmResult,
    AlgorithmResultProvenance,
    CausalEffect,
    Diagnostics,
    EvidenceResult,
    GraphEdge,
    InvocationRecord,
    StandardizedGraph,
    WebEvidenceResult,
)


def _decision():
    return {
        "outcome": "algorithm_supported",
        "primary_result_ref": "result-1",
        "result_assessments": [
            {"result_ref": "result-1", "disposition": "primary", "rationale": "valid"}
        ],
        "conflict_status": "none",
        "conflicts": [],
        "revision_proposals": [],
        "selection_rationale": "selected by evidence",
        "confidence": "medium",
        "confidence_basis": ["diagnostics"],
    }


def test_structured_response_and_reference_closure_are_required() -> None:
    decision = validate_structured_response(_decision())
    assert decision.primary_result_ref == "result-1"
    validate_decision_references(decision, algorithm_result_refs={"result-1"})
    with pytest.raises(StructuredResponseError, match="unknown algorithm result"):
        validate_decision_references(decision, algorithm_result_refs=set())


def test_retry_budget_allows_exactly_one_retry() -> None:
    controller = FinalizationRetryController()
    assert controller.can_retry
    assert controller.consume() == 1
    assert not controller.can_retry
    with pytest.raises(StructuredResponseError, match="exhausted"):
        controller.consume()


def _algorithm_execution(*, failure: bool = False):
    identity = TrustedJobIdentity(
        job_id="00000000-0000-0000-0000-000000000201",
        session_id="00000000-0000-0000-0000-000000000202",
        user_id=7,
        attempt_count=0,
        lease_epoch=1,
        worker_id="worker-1",
        input_identity="input-sha",
    )
    csv_data = PC_INSUFFICIENT_SAMPLE_CSV if failure else PC_CSV
    identity = bind_analysis_input(identity, csv_data)
    executor = LocalAlgorithmExecutor(csv_data)
    backend = build_in_memory_backend(user_id=7)
    context = AgentRunContext(
        execution_guard=None,
        trusted_identity=identity,
        algorithm_executor=executor,
        filesystem_backend=backend,
    )
    registry = build_default_registry(
        build_default_adapters(executor=executor, raw_backend=backend)
    )
    tool = next(
        item
        for item in build_algorithm_tools(
            registry,
            runtime_context=context,
            data_profile=executor.data_profile,
        )
        if item.name == "causal_pc"
    ).to_langchain_tool()
    command = asyncio.run(
        tool.coroutine(
            runtime=ToolRuntime(
                tool_call_id="gate-call-1",
                state={
                    "message_execution_id": "gate-response-1",
                    "data_profile": executor.data_profile,
                },
                context=context, config={}, stream_writer=[].append, store=None,
            ),
            alpha=0.05,
        )
    )
    result = next(iter(command.update["algorithm_results"].values()))
    ledger = next(iter(command.update["action_ledger"].values()))
    return identity, registry, result, ledger


def _evidence_only_decision():
    return validate_structured_response(
        {
            **_decision(),
            "outcome": "evidence_only",
            "primary_result_ref": None,
            "result_assessments": [],
        }
    )


def _web_ledger(identity, *, final_status="succeeded", attempt_status="succeeded"):
    return _evidence_ledger(
        identity,
        tool_name="web_evidence_search",
        final_status=final_status,
        attempt_status=attempt_status,
    )


def _evidence_ledger(
    identity,
    *,
    tool_name,
    final_status="succeeded",
    attempt_status="succeeded",
):
    response_identity = f"{tool_name}-response-1"
    provider_call_id = f"{tool_name}-call-1"
    invocation_id = build_invocation_id(
        job_id=identity.job_id,
        response_identity=response_identity,
        provider_call_id=provider_call_id,
    )
    started_at = datetime.now(timezone.utc)
    record = InvocationRecord(
        invocation_id=invocation_id,
        response_identity=response_identity,
        response_identity_source="message_execution_id",
        provider_call_id=provider_call_id,
        tool_name=tool_name,
        final_status=final_status,
        job_id=str(identity.job_id),
        attempt_count=identity.attempt_count,
        lease_epoch=identity.lease_epoch,
        worker_id=identity.worker_id,
        input_identity=identity.current_input_identity(),
        source_input_identity=identity.source_input_identity(),
        analysis_dataset_id=identity.analysis_input.dataset_id,
        attempts={
            0: ActionAttempt(
                retry_ordinal=0,
                revision=2,
                status=attempt_status,
                started_at=started_at,
                finished_at=started_at,
            )
        },
    )
    return record


_EFFECT_PARAMETERS = {"target": "y", "treatment": "x", "columns": ["x", "y"]}
_EFFECT_QUESTION = "请分析 x 对 y 的因果效应。"


def _ate_attempt(identity, registry, *, status="not_ready"):
    """构造一次真实的 causal_ate 终态，用于覆盖“尝试即放行”。"""

    spec = registry.get("causal.ate").spec
    response_identity = "causal_ate-response-1"
    provider_call_id = "causal_ate-call-1"
    invocation_id = build_invocation_id(
        job_id=identity.job_id,
        response_identity=response_identity,
        provider_call_id=provider_call_id,
    )
    result = AlgorithmResult(
        result_ref=build_result_ref(invocation_id=invocation_id),
        invocation_id=invocation_id,
        provider_call_id=provider_call_id,
        capability_id=spec.capability_id,
        capability_version=spec.version,
        status=status,
        diagnostics=Diagnostics(
            safe_error_code=f"ate_{status}",
            summary="ATE 未产生估计结果。",
        ),
        provenance=AlgorithmResultProvenance(
            job_id=str(identity.job_id),
            attempt_count=identity.attempt_count,
            lease_epoch=identity.lease_epoch,
            input_identity=identity.current_input_identity(),
            source_input_identity=identity.source_input_identity(),
            analysis_dataset_id=identity.analysis_input.dataset_id,
            spec_digest=spec.spec_digest,
            invocation_id=invocation_id,
            capability_id=spec.capability_id,
            capability_version=spec.version,
        ),
    )
    started_at = datetime.now(timezone.utc)
    record = InvocationRecord(
        invocation_id=invocation_id,
        response_identity=response_identity,
        response_identity_source="message_execution_id",
        provider_call_id=provider_call_id,
        tool_name="causal_ate",
        final_status=status,
        job_id=str(identity.job_id),
        attempt_count=identity.attempt_count,
        lease_epoch=identity.lease_epoch,
        worker_id=identity.worker_id,
        input_identity=identity.current_input_identity(),
        source_input_identity=identity.source_input_identity(),
        analysis_dataset_id=identity.analysis_input.dataset_id,
        attempts={
            0: ActionAttempt(
                retry_ordinal=0,
                revision=2,
                status=status,
                started_at=started_at,
                finished_at=started_at,
            )
        },
    )
    return result, record


def _primary_decision(result):
    return validate_structured_response(
        {
            **_decision(),
            "primary_result_ref": result.result_ref,
            "result_assessments": [
                {
                    "result_ref": result.result_ref,
                    "disposition": "primary",
                    "rationale": "该结果通过稳定性诊断。",
                }
            ],
        }
    )


def test_finalization_gate_requires_algorithm_result_on_analysis_route() -> None:
    """分析路由下不允许一个算法都没跑就交证据型结论。"""

    identity, registry, _result, _ledger = _algorithm_execution()
    decision = _evidence_only_decision()

    with pytest.raises(StructuredResponseError, match="requires at least one algorithm result"):
        FinalizationGate(registry=registry).validate(
            decision=decision,
            algorithm_results={},
            action_ledger={},
            trusted_identity=identity,
            route_decision="fold",
        )

    accepted = FinalizationGate(registry=registry).validate(
        decision=decision,
        algorithm_results={},
        action_ledger={},
        trusted_identity=identity,
        route_decision="inquiry_answer",
    )
    assert accepted.outcome == "evidence_only"


def test_finalization_gate_allows_discarded_result_on_analysis_route() -> None:
    """算法确实跑过（哪怕结果被丢弃）时不触发“必须调用算法”规则。"""

    identity, registry, result, ledger = _algorithm_execution()
    decision = validate_structured_response(
        {
            **_decision(),
            "outcome": "evidence_only",
            "primary_result_ref": None,
            "result_assessments": [
                {
                    "result_ref": result.result_ref,
                    "disposition": "discarded",
                    "rationale": "该结果不作为结论依据。",
                }
            ],
        }
    )

    accepted = FinalizationGate(registry=registry).validate(
        decision=decision,
        algorithm_results={result.result_ref: result},
        action_ledger={
            ledger.invocation_id: ledger,
        },
        trusted_identity=identity,
        route_decision="fold",
    )
    assert accepted.outcome == "evidence_only"


def test_finalization_gate_allows_analysis_without_rag_retrieval() -> None:
    identity, registry, result, ledger = _algorithm_execution()
    decision = validate_structured_response(
        {
            **_decision(),
            "primary_result_ref": result.result_ref,
            "result_assessments": [
                {
                    "result_ref": result.result_ref,
                    "disposition": "primary",
                    "rationale": "the validated result is selected",
                }
            ],
        }
    )

    accepted = FinalizationGate(registry=registry).validate(
        decision=decision,
        algorithm_results={result.result_ref: result},
        action_ledger={ledger.invocation_id: ledger},
        trusted_identity=identity,
        route_decision="fold",
    )
    assert accepted.primary_result_ref == result.result_ref


def test_finalization_gate_rejects_nonterminal_evidence_ledger() -> None:
    identity, registry, result, ledger = _algorithm_execution()
    web_ledger = _web_ledger(
        identity,
        final_status="succeeded",
        attempt_status="running",
    )
    decision = validate_structured_response(
        {
            **_decision(),
            "primary_result_ref": result.result_ref,
            "result_assessments": [
                {
                    "result_ref": result.result_ref,
                    "disposition": "primary",
                    "rationale": "the validated result is selected",
                }
            ],
        }
    )

    with pytest.raises(
        StructuredResponseError,
        match="evidence action ledger terminal status mismatch",
    ):
        FinalizationGate(registry=registry).validate(
            decision=decision,
            algorithm_results={result.result_ref: result},
            action_ledger={
                ledger.invocation_id: ledger,
                web_ledger.invocation_id: web_ledger,
            },
            trusted_identity=identity,
            route_decision="fold",
            web_search_enabled=True,
        )


def test_finalization_gate_requires_enabled_web_search_on_analysis_route() -> None:
    identity, registry, result, ledger = _algorithm_execution()
    decision = validate_structured_response(
        {
            **_decision(),
            "primary_result_ref": result.result_ref,
            "result_assessments": [
                {
                    "result_ref": result.result_ref,
                    "disposition": "primary",
                    "rationale": "the validated result is selected",
                }
            ],
        }
    )

    with pytest.raises(
        StructuredResponseError,
        match="requires at least one web evidence invocation",
    ):
        FinalizationGate(registry=registry).validate(
            decision=decision,
            algorithm_results={result.result_ref: result},
            action_ledger={ledger.invocation_id: ledger},
            trusted_identity=identity,
            route_decision="fold",
            web_search_enabled=True,
        )

    web_ledger = _web_ledger(identity)
    accepted = FinalizationGate(registry=registry).validate(
        decision=decision,
        algorithm_results={result.result_ref: result},
        action_ledger={
            ledger.invocation_id: ledger,
            web_ledger.invocation_id: web_ledger,
        },
        trusted_identity=identity,
        route_decision="fold",
        web_search_enabled=True,
    )
    assert accepted.primary_result_ref == result.result_ref


def test_finalization_gate_accepts_only_ledger_backed_primary_result() -> None:
    identity, registry, result, ledger = _algorithm_execution()
    decision = validate_structured_response(
        {
            **_decision(),
            "primary_result_ref": result.result_ref,
            "result_assessments": [
                {
                    "result_ref": result.result_ref,
                    "disposition": "primary",
                    "rationale": "the validated result is selected",
                }
            ],
        }
    )

    accepted = FinalizationGate(registry=registry).validate(
        decision=decision,
        algorithm_results={result.result_ref: result},
        action_ledger={ledger.invocation_id: ledger},
        trusted_identity=identity,
    )

    assert accepted.primary_result_ref == result.result_ref


def _valid_ate_result(identity, registry):
    """构造一次有效的后门估计结果；图沿用源 DAG 语义。"""

    spec = registry.get("causal.ate").spec
    response_identity = "causal_ate-response-1"
    provider_call_id = "causal_ate-call-1"
    invocation_id = build_invocation_id(
        job_id=identity.job_id,
        response_identity=response_identity,
        provider_call_id=provider_call_id,
    )
    result_ref = build_result_ref(invocation_id=invocation_id)
    result = AlgorithmResult(
        result_ref=result_ref,
        invocation_id=invocation_id,
        provider_call_id=provider_call_id,
        capability_id=spec.capability_id,
        capability_version=spec.version,
        status="valid",
        standardized_graph=StandardizedGraph(
            graph_semantics="dag_target_to_source",
            nodes=["x", "y"],
            edges=[GraphEdge(source="x", target="y")],
        ),
        causal_effect=CausalEffect(
            estimand="ATE",
            graph_result_ref="source-result:0",
            treatment="x",
            outcome="y",
            estimate=1.0,
            standard_error=0.1,
            ci95=(0.8, 1.2),
            estimator="backdoor_ols",
            n_rows=100,
        ),
        provenance=AlgorithmResultProvenance(
            job_id=str(identity.job_id),
            attempt_count=identity.attempt_count,
            lease_epoch=identity.lease_epoch,
            input_identity=identity.current_input_identity(),
            source_input_identity=identity.source_input_identity(),
            analysis_dataset_id=identity.analysis_input.dataset_id,
            spec_digest=spec.spec_digest,
            invocation_id=invocation_id,
            capability_id=spec.capability_id,
            capability_version=spec.version,
        ),
    )
    started_at = datetime.now(timezone.utc)
    record = InvocationRecord(
        invocation_id=invocation_id,
        response_identity=response_identity,
        response_identity_source="message_execution_id",
        provider_call_id=provider_call_id,
        tool_name="causal_ate",
        final_status="succeeded",
        job_id=str(identity.job_id),
        attempt_count=identity.attempt_count,
        lease_epoch=identity.lease_epoch,
        worker_id=identity.worker_id,
        input_identity=identity.current_input_identity(),
        source_input_identity=identity.source_input_identity(),
        analysis_dataset_id=identity.analysis_input.dataset_id,
        attempts={
            0: ActionAttempt(
                retry_ordinal=0,
                revision=2,
                status="succeeded",
                started_at=started_at,
                finished_at=started_at,
            )
        },
    )
    return result, record


def test_finalization_gate_accepts_estimation_result_as_primary() -> None:
    identity, registry, _result, _ledger = _algorithm_execution()
    result, record = _valid_ate_result(identity, registry)
    decision = validate_structured_response(
        {
            **_decision(),
            "primary_result_ref": result.result_ref,
            "result_assessments": [
                {
                    "result_ref": result.result_ref,
                    "disposition": "primary",
                    "rationale": "效应估计是本问题的主结果",
                }
            ],
        }
    )

    accepted = FinalizationGate(registry=registry).validate(
        decision=decision,
        algorithm_results={result.result_ref: result},
        action_ledger={record.invocation_id: record},
        trusted_identity=identity,
    )

    assert accepted.primary_result_ref == result.result_ref


@pytest.mark.parametrize("changed,reason", [
    ("dataset", "analysis dataset identity mismatch"),
    ("content", "no matching ledger record"),
    ("source", "analysis dataset identity mismatch"),
])
def test_finalization_gate_rejects_results_after_analysis_input_changes(changed, reason) -> None:
    identity, registry, result, ledger = _algorithm_execution()
    decision = validate_structured_response({
        **_decision(),
        "primary_result_ref": result.result_ref,
        "result_assessments": [{
            "result_ref": result.result_ref, "disposition": "primary",
            "rationale": "采用已完成的 PC 结果。",
        }],
    })
    if changed == "dataset":
        updated = bind_analysis_input(identity, dataset_id=2)
    elif changed == "content":
        rows = PC_CSV.splitlines()
        updated = bind_analysis_input(identity, "\n".join([rows[0], *reversed(rows[1:])]) + "\n")
    else:
        updated = replace(identity, input_identity=identity.current_input_identity())
    with pytest.raises(StructuredResponseError, match=reason):
        FinalizationGate(registry=registry).validate(
            decision=decision,
            algorithm_results={result.result_ref: result},
            action_ledger={ledger.invocation_id: ledger},
            trusted_identity=updated,
        )


def test_gate_validated_decision_is_projected_with_public_algorithm_names() -> None:
    identity, registry, result, _ledger = _algorithm_execution()
    decision = validate_structured_response({
        **_decision(),
        "primary_result_ref": result.result_ref,
        "result_assessments": [{
            "result_ref": result.result_ref,
            "disposition": "primary",
            "rationale": "该结果通过稳定性诊断。",
        }],
        "selection_rationale": "诊断结果支持该选择。",
    })
    events = []

    _emit_public_final_decision(
        runtime=type("Runtime", (), {"stream_writer": events.append})(),
        identity=identity,
        decision=decision,
        algorithm_results={result.result_ref: result},
        registry=registry,
    )

    assert len(events) == 1
    assert events[0]["decision_kind"] == "final"
    assert "PC 因果发现：主结果" in events[0]["summary"]
    assert "该结果通过稳定性诊断" in events[0]["summary"]
    assert "置信度：中等" in events[0]["summary"]
    assert events[0]["_event_key"].startswith("deep-agent-final-decision:0:")
    assert result.result_ref not in repr(events[0])


def test_finalization_gate_rejects_incomplete_current_ledger_ownership() -> None:
    identity, registry, result, ledger = _algorithm_execution()
    decision = validate_structured_response(
        {
            **_decision(),
            "primary_result_ref": result.result_ref,
            "result_assessments": [
                {
                    "result_ref": result.result_ref,
                    "disposition": "primary",
                    "rationale": "the validated result is selected",
                }
            ],
        }
    )
    incomplete = ledger.model_copy(deep=True, update={"worker_id": None})

    with pytest.raises(StructuredResponseError, match="ownership is incomplete"):
        FinalizationGate(registry=registry).validate(
            decision=decision,
            algorithm_results={result.result_ref: result},
            action_ledger={ledger.invocation_id: incomplete},
            trusted_identity=identity,
        )


def test_finalization_gate_excludes_stale_attempt_ledger_from_current_results() -> None:
    identity, registry, result, ledger = _algorithm_execution()
    decision = validate_structured_response(
        {
            **_decision(),
            "primary_result_ref": result.result_ref,
            "result_assessments": [
                {
                    "result_ref": result.result_ref,
                    "disposition": "primary",
                    "rationale": "the validated result is selected",
                }
            ],
        }
    )
    stale = ledger.model_copy(
        deep=True,
        update={"attempt_count": identity.attempt_count + 1},
    )

    with pytest.raises(StructuredResponseError, match="matching ledger"):
        FinalizationGate(registry=registry).validate(
            decision=decision,
            algorithm_results={result.result_ref: result},
            action_ledger={ledger.invocation_id: stale},
            trusted_identity=identity,
        )


def test_finalization_gate_requires_every_valid_result_assessment() -> None:
    identity, registry, result, ledger = _algorithm_execution()
    decision = validate_structured_response(
        {
            **_decision(),
            "outcome": "evidence_only",
            "primary_result_ref": None,
            "result_assessments": [],
        }
    )

    with pytest.raises(StructuredResponseError, match="each eligible valid result"):
        FinalizationGate(registry=registry).validate(
            decision=decision,
            algorithm_results={result.result_ref: result},
            action_ledger={ledger.invocation_id: ledger},
            trusted_identity=identity,
        )


def test_finalization_gate_accepts_no_valid_algorithm_only_after_a_failed_call() -> None:
    identity, registry, result, ledger = _algorithm_execution(failure=True)
    decision = validate_structured_response(
        {
            "outcome": "no_valid_algorithm",
            "primary_result_ref": None,
            "result_assessments": [],
            "conflict_status": "none",
            "conflicts": [],
            "revision_proposals": [],
            "selection_rationale": "the algorithm call did not produce a valid result",
            "confidence": "low",
            "confidence_basis": ["execution ledger"],
        }
    )

    accepted = FinalizationGate(registry=registry).validate(
        decision=decision,
        algorithm_results={result.result_ref: result},
        action_ledger={ledger.invocation_id: ledger},
        trusted_identity=identity,
    )

    assert accepted.outcome == "no_valid_algorithm"


def test_effect_rule_uses_one_retry_then_degrades() -> None:
    identity, registry, result, ledger = _algorithm_execution()
    gate = FinalizationGate(registry=registry)
    runtime = SimpleNamespace(
        context=AgentRunContext(execution_guard=None, trusted_identity=identity),
        stream_writer=[].append,
    )
    state = {
        "deep_agent_structured_response": _primary_decision(result),
        "deep_agent_algorithm_results": {result.result_ref: result},
        "deep_agent_action_ledger": {ledger.invocation_id: ledger},
        "route_decision": "fold",
        "analysis_parameters": _EFFECT_PARAMETERS,
        "messages": [HumanMessage(content=_EFFECT_QUESTION)],
    }

    retried = asyncio.run(
        _finalization_gate_node(state, runtime=runtime, config={}, gate=gate)
    )
    assert retried["finalization_retry_count"] == 1
    assert "causal_ate" in retried["deep_agent_retry_instruction"]

    degraded = asyncio.run(
        _finalization_gate_node(
            {**state, "finalization_retry_count": 1},
            runtime=runtime,
            config={},
            gate=gate,
        )
    )
    assert degraded["finalization_status"] == "degraded"
    assert degraded["finalization_error"] == "FINALIZATION_CONTRACT_INVALID"


def test_no_valid_algorithm_hint_still_demands_ate_for_effect_question() -> None:
    identity, registry, result, ledger = _algorithm_execution()
    decision = validate_structured_response(
        {
            **_decision(),
            "outcome": "no_valid_algorithm",
            "primary_result_ref": None,
            "result_assessments": [
                {
                    "result_ref": result.result_ref,
                    "disposition": "discarded",
                    "rationale": "该图不适用。",
                }
            ],
        }
    )

    with pytest.raises(StructuredResponseError) as failure:
        FinalizationGate(registry=registry).validate(
            decision=decision,
            algorithm_results={result.result_ref: result},
            action_ledger={ledger.invocation_id: ledger},
            trusted_identity=identity,
            route_decision="fold",
            analysis_parameters=_EFFECT_PARAMETERS,
            analysis_question=_EFFECT_QUESTION,
        )

    assert failure.value.rule == "no_valid_algorithm_with_valid_result"
    instruction = build_finalization_retry_instruction(failure.value)
    assert "causal_ate" in instruction


def test_gate_rejects_evidence_reference_mixed_into_result_assessments() -> None:
    """复现生产事故：证据引用写进 assessments，算法引用写进 evidence_refs。"""

    identity, registry, result, ledger = _algorithm_execution()
    evidence_ref = (
        "rag:mm_" + "a" * 20 + ":E1:invocation:afd5b0d2-7a81-535c-bf89-9a59178a599a"
    )
    algorithm_results = {result.result_ref: result}
    action_ledger = {ledger.invocation_id: ledger}
    rag_evidence = {
        evidence_ref: EvidenceResult(evidence_ref=evidence_ref, snippet="无关证据"),
    }
    gate = FinalizationGate(registry=registry)

    mixed_up = validate_structured_response(
        {
            "outcome": "algorithm_supported",
            "primary_result_ref": result.result_ref,
            "result_assessments": [
                {
                    "result_ref": result.result_ref,
                    "disposition": "primary",
                    "rationale": "该结果通过稳定性诊断。",
                },
                {
                    "result_ref": evidence_ref,
                    "disposition": "discarded",
                    "rationale": "该证据与本次变量无关。",
                },
            ],
            "conflict_status": "none",
            "conflicts": [],
            "revision_proposals": [
                {
                    "result_ref": result.result_ref,
                    "action": "retain",
                    "rationale": "保留主图。",
                    "evidence_refs": [result.result_ref],
                }
            ],
            "selection_rationale": "以该主结果作为主图。",
            "confidence": "medium",
            "confidence_basis": ["execution ledger"],
        }
    )

    with pytest.raises(StructuredResponseError) as assessment_failure:
        gate.validate(
            decision=mixed_up,
            algorithm_results=algorithm_results,
            action_ledger=action_ledger,
            trusted_identity=identity,
            rag_evidence=rag_evidence,
        )
    assert assessment_failure.value.rule == "assessment_ref_unknown_result"
    first_instruction = build_finalization_retry_instruction(assessment_failure.value)
    assert "result_assessments 只能引用本次运行返回的算法结果" in first_instruction
    assert "revision_proposals.evidence_refs" in first_instruction

    # 只修第一处会立刻暴露第二处：evidence_refs 里填了算法结果引用。
    without_evidence_assessment = validate_structured_response(
        {
            **mixed_up.model_dump(mode="json"),
            "result_assessments": [
                {
                    "result_ref": result.result_ref,
                    "disposition": "primary",
                    "rationale": "该结果通过稳定性诊断。",
                }
            ],
        }
    )
    with pytest.raises(StructuredResponseError) as proposal_failure:
        gate.validate(
            decision=without_evidence_assessment,
            algorithm_results=algorithm_results,
            action_ledger=action_ledger,
            trusted_identity=identity,
            rag_evidence=rag_evidence,
        )
    assert proposal_failure.value.rule == "proposal_evidence_ref_unknown"
    assert "算法结果之间互相印证请写进 rationale" in (
        build_finalization_retry_instruction(proposal_failure.value)
    )

    corrected = validate_structured_response(
        {
            **without_evidence_assessment.model_dump(mode="json"),
            "revision_proposals": [
                {
                    "result_ref": result.result_ref,
                    "action": "retain",
                    "rationale": "保留主图。",
                    "evidence_refs": [],
                }
            ],
        }
    )
    accepted = gate.validate(
        decision=corrected,
        algorithm_results=algorithm_results,
        action_ledger=action_ledger,
        trusted_identity=identity,
        rag_evidence=rag_evidence,
    )
    assert accepted.primary_result_ref == result.result_ref


def test_retry_instruction_stays_generic_for_internal_failures() -> None:
    """身份/账本完整性失败不带规则码，避免把内部问题交还给模型修正。"""

    error = StructuredResponseError("action ledger ownership is incomplete")
    assert error.rule is None
    instruction = build_finalization_retry_instruction(error)
    assert "具体规则" not in instruction
    assert "不要重新编造工具调用或结果" in instruction


def test_deep_agent_evidence_projection_keeps_report_snippets_and_sources() -> None:
    rag = EvidenceResult(
        evidence_ref="rag:1",
        snippet="RAG snippet",
        source_title="Knowledge source",
        source_url="https://example.invalid/rag",
        release_id="mm_" + "a" * 20,
    )
    web = WebEvidenceResult(
        evidence_ref="web:1",
        snippet="Web snippet",
        source_title="Web source",
        source_url="https://example.invalid/web",
        provider_status="available",
        fetched_at="2026-09-15T00:00:00Z",
    )

    rag_result = _legacy_rag_evidence_result({"rag:1": rag})
    web_result = _legacy_web_evidence_result({"web:1": web})

    assert rag_result["success"] is True
    assert "RAG snippet" in rag_result["questions"][0]["answer"]
    assert rag_result["questions"][0]["citations"] == ["rag:1"]
    assert web_result["success"] is True
    assert web_result["content"][0]["text"] == "Web snippet"
    assert web_result["content"][0]["url"] == "https://example.invalid/web"


def test_effect_predicate_requires_distinct_bound_variables() -> None:
    assert _requires_effect_estimation(_EFFECT_PARAMETERS)
    assert not _requires_effect_estimation({"target": "y"})
    assert not _requires_effect_estimation({"target": "y", "treatment": " "})
    assert not _requires_effect_estimation(
        {"target": "y", "treatment": "y", "columns": ["x", "y"]},
    )
    assert not _requires_effect_estimation(
        {"target": "y", "treatment": "z", "columns": ["x", "y"]},
    )
    assert not _requires_effect_estimation(None)


@pytest.mark.parametrize(
    "question",
    [_EFFECT_QUESTION, "Estimate the causal effect of x on y.", None],
)
def test_gate_requires_ate_attempt_whenever_variables_are_bound(question) -> None:
    identity, registry, result, ledger = _algorithm_execution()

    with pytest.raises(StructuredResponseError) as failure:
        FinalizationGate(registry=registry).validate(
            decision=_primary_decision(result),
            algorithm_results={result.result_ref: result},
            action_ledger={ledger.invocation_id: ledger},
            trusted_identity=identity,
            route_decision="fold",
            analysis_parameters=_EFFECT_PARAMETERS,
            analysis_question=question,
        )

    assert failure.value.rule == "effect_question_without_ate_attempt"
    instruction = build_finalization_retry_instruction(failure.value)
    assert "causal_ate" in instruction
    assert "evidence_only" in instruction


@pytest.mark.parametrize("ate_status", ["not_ready", "not_applicable"])
def test_gate_accepts_effect_question_after_ate_attempt(ate_status) -> None:
    identity, registry, result, ledger = _algorithm_execution()
    ate_result, ate_record = _ate_attempt(identity, registry, status=ate_status)

    accepted = FinalizationGate(registry=registry).validate(
        decision=_primary_decision(result),
        algorithm_results={
            result.result_ref: result,
            ate_result.result_ref: ate_result,
        },
        action_ledger={
            ledger.invocation_id: ledger,
            ate_record.invocation_id: ate_record,
        },
        trusted_identity=identity,
        route_decision="fold",
        analysis_parameters=_EFFECT_PARAMETERS,
        analysis_question=_EFFECT_QUESTION,
    )

    assert accepted.primary_result_ref == result.result_ref


@pytest.mark.parametrize(
    "parameters,question",
    [
        ({"target": "y", "columns": ["x", "y"]}, _EFFECT_QUESTION),
        (
            {"target": "y", "treatment": "y", "columns": ["x", "y"]},
            _EFFECT_QUESTION,
        ),
        (
            {"target": "y", "treatment": "z", "columns": ["x", "y"]},
            _EFFECT_QUESTION,
        ),
    ],
)
def test_gate_skips_ate_requirement_when_trigger_is_incomplete(parameters, question) -> None:
    identity, registry, result, ledger = _algorithm_execution()

    accepted = FinalizationGate(registry=registry).validate(
        decision=_primary_decision(result),
        algorithm_results={result.result_ref: result},
        action_ledger={ledger.invocation_id: ledger},
        trusted_identity=identity,
        route_decision="fold",
        analysis_parameters=parameters,
        analysis_question=question,
    )

    assert accepted.primary_result_ref == result.result_ref


def test_gate_skips_ate_requirement_without_valid_graph() -> None:
    identity, registry, result, ledger = _algorithm_execution(failure=True)
    decision = validate_structured_response(
        {
            "outcome": "no_valid_algorithm",
            "primary_result_ref": None,
            "result_assessments": [],
            "conflict_status": "none",
            "conflicts": [],
            "revision_proposals": [],
            "selection_rationale": "算法调用没有产生有效结果。",
            "confidence": "low",
            "confidence_basis": ["execution ledger"],
        }
    )

    accepted = FinalizationGate(registry=registry).validate(
        decision=decision,
        algorithm_results={result.result_ref: result},
        action_ledger={ledger.invocation_id: ledger},
        trusted_identity=identity,
        route_decision="fold",
        analysis_parameters=_EFFECT_PARAMETERS,
        analysis_question=_EFFECT_QUESTION,
    )

    assert accepted.outcome == "no_valid_algorithm"

