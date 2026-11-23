"""Run real discovery kernels and check Gate, saved facts, and public reports offline."""

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from langgraph.errors import NodeError
from pydantic import ValidationError

from Agent.CausalAgentMCP.runner_registry import build_default_registry
from Agent.Report.document import ReportDraft, build_report_document, parse_report_document
from Agent.Report.time_series import build_time_series_overview, with_time_series_report_block
from Agent.causal_agent.analysis_context import (
    build_context_commit, build_report_analysis_context, context_graph, report_primary_result,
)
from Agent.causal_agent.fault_tolerance import recover_report
from Agent.causal_agent.nodes import _prompt_analysis_facts, _report_graph_asset, _report_execution_summary
from Agent.data_processing.parameters import parameter_identity
from Agent.deep_agent.context import AnalysisInputRef, AgentRunContext, TrustedJobIdentity
from Agent.deep_agent.finalization import FinalizationGate, StructuredResponseError
from Agent.deep_agent.state import to_deep_agent_input
from Agent.deep_agent_tools.algorithm_specs import get_algorithm_spec
from Agent.deep_agent_tools.identity import build_invocation_id
from Agent.deep_agent_tools.models import (
    AlgorithmExecutionCommand, FinalAnalysisDecision, build_raw_result_metadata, canonical_json_bytes,
)
from Agent.deep_agent_tools.normalization import result_from_runner_payload
from Agent.deep_agent_tools.runtime_updates import (
    RuntimeInvocationIdentity, build_terminal_invocation, normalize_tool_status,
)
from Agent.deep_agent_tools.time_series import TimeSeriesResult
from app.agent.worker.result_presenter import process_final_result


TIME_SERIES_PARAMS = {"tau_min": 0, "tau_max": 3, "pc_alpha": 0.05}


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _input(frame, *, source_bytes=None, times=None):
    if times is None:
        times = pd.date_range("2026-01-01", periods=len(frame), freq="5min", tz="UTC")
    if source_bytes is None:
        source_bytes = frame.assign(Time=times).to_csv(index=False).encode()
    return frame.to_csv(index=False), {
        "time_column": "Time", "start_time": times[0].isoformat(), "end_time": times[-1].isoformat(),
        "sampling_period_seconds": 300, "row_count": len(frame),
        "source_sha256": _hash(source_bytes), "selection_rule": "longest_contiguous_then_earliest",
    }


@pytest.fixture(scope="module")
def calculation():
    pytest.importorskip("tigramite")
    rng = np.random.default_rng(123)
    n = 500
    x, y, z = rng.normal(size=(3, n))
    for i in range(3, n):
        x[i] += .8 * x[i - 1]
        y[i] += 1.1 * x[i - 1] + .9 * x[i - 3]
        z[i] += .8 * y[i]
    return _input(pd.DataFrame({"X|sensor": x, "Y": y, "Z": z}))


def _identity(csv, axis, goal="time_series"):
    params = {"effect_request": None, "discovery_goal": goal}
    analysis_input = AnalysisInputRef.create(
        dataset_id=1, object_id=1, content_hash=_hash(csv.encode()), filename="analysis.csv",
        research_identity=parameter_identity(params), time_series=axis if goal == "time_series" else None,
    )
    return params, TrustedJobIdentity(
        job_id=uuid4(), session_id=uuid4(), user_id=1, attempt_count=1, lease_epoch=1,
        worker_id="offline-real-calculation", input_identity=axis["source_sha256"],
        analysis_input=analysis_input,
    )


def _execute(identity, capability, csv, parameters, artifacts, *, command_parameters=None, raw_dir=None):
    spec = get_algorithm_spec(capability)
    registry = build_default_registry()
    runtime_identity = RuntimeInvocationIdentity(
        provider_call_id=spec.tool_name, response_identity="offline-runner", response_identity_source="provider_response_id",
        provider_response_id="offline-runner", runtime_context=AgentRunContext(execution_guard=None, trusted_identity=identity),
    )
    invocation_id = build_invocation_id(
        job_id=identity.job_id, response_identity=runtime_identity.response_identity,
        provider_call_id=runtime_identity.provider_call_id,
    )
    started = datetime.now(timezone.utc)
    runner = registry.resolve(capability, spec.version, spec.spec_digest)
    payload = runner.runner(csv, parameters, artifacts)
    actual_parameters = parameters if command_parameters is None else command_parameters
    result = result_from_runner_payload(
        payload, context=identity.to_mcp_context(invocation_id=invocation_id), capability_version=spec.version,
        command=AlgorithmExecutionCommand(
            invocation_id=invocation_id, capability_id=capability, capability_version=spec.version,
            spec_digest=spec.spec_digest, provider_call_id=spec.tool_name,
            input_identity=identity.current_input_identity(), parameters=actual_parameters,
        ),
    )
    if raw_dir is not None:
        raw_ref = f"/raw_algorithm_results/{invocation_id}/0.json"
        raw_path = raw_dir / raw_ref.lstrip("/")
        raw_path.parent.mkdir(parents=True)
        raw_path.write_bytes(canonical_json_bytes(payload))
        metadata = build_raw_result_metadata(raw_result_ref=raw_ref, raw_result=payload)
        assert _hash(raw_path.read_bytes()) == metadata.raw_result_sha256
        result = type(result).model_validate({**result.model_dump(), **metadata.model_dump()})
    record = build_terminal_invocation(
        identity=runtime_identity, tool_name=spec.tool_name, started_at=started,
        attempt_status=normalize_tool_status(result.status), result_ref=result.result_ref,
    )
    return result, record


def _accept(params, identity, executions, *, primary=None, exhausted=False):
    results = {result.result_ref: result for result, _ in executions}
    ledger = {record.invocation_id: record for _, record in executions}
    decision = FinalAnalysisDecision(
        outcome="algorithm_supported" if primary else "no_valid_algorithm", primary_graph_result_ref=primary,
        result_assessments=[{
            "result_ref": ref, "rationale": "按已确认研究目标评估。",
            "disposition": "primary_graph" if ref == primary else "supporting" if result.status == "valid" else "discarded",
        } for ref, result in results.items()],
        conflict_status="none", selection_rationale="按实际执行与可用结果选择。", confidence="low",
    )
    accepted = FinalizationGate().validate(
        decision=decision, algorithm_results=results, action_ledger=ledger, trusted_identity=identity,
        route_decision="data_processing", analysis_parameters=params, analysis_budget_exhausted=exhausted,
    )
    return {"analysis_parameters": params, "deep_agent_algorithm_results": results, "deep_agent_action_ledger": ledger,
            "deep_agent_accepted_decision": accepted, "finalization_status": "valid", "deep_agent_budget_exhausted": exhausted}


def _render(state):
    facts = build_report_analysis_context(state)
    primary = report_primary_result(facts)
    overview = build_time_series_overview(primary)
    blocks = with_time_series_report_block(
        [], discovery_goal=facts.get("discovery_goal"), primary=primary, overview=overview,
    )
    document = build_report_document(ReportDraft(title="时序报告", blocks=blocks),
                                     assets={overview.graph_id: overview} if overview else None,
                                     execution_summary=_report_execution_summary(state))
    payload = process_final_result({"report_document": document})
    return parse_report_document(json.loads(json.dumps(payload["document"])))


def _regeneration(state):
    commit = json.loads(json.dumps(build_context_commit(state)))
    context = {"latest_algorithm_summary": commit["algorithm_summary"]}
    assert context_graph(context) is None
    return {"report_revision_mode": "full_regeneration_from_context", "analysis_context": context}


def _findings(document):
    return next(block["content"] for block in document["blocks"] if block["id"] == "time_series_findings")


def test_run_preserves_distinct_lags_and_self_links_through_report_regeneration(calculation, tmp_path):
    csv, axis = calculation
    params, identity = _identity(csv, axis)
    result, record = _execute(identity, "causal.pcmci_plus", csv, TIME_SERIES_PARAMS, {"time_series": axis}, raw_dir=tmp_path)
    assert result.status == "valid"
    assert result.standardized_graph is None
    assert {(r.source, r.target, r.lag_steps) for r in result.time_series.relations} >= {
        ("X|sensor", "X|sensor", 1), ("X|sensor", "Y", 1), ("X|sensor", "Y", 3),
    }
    state = _accept(params, identity, [(result, record)], primary=result.result_ref)
    restored = _regeneration(state)
    facts = build_report_analysis_context(state)
    assert build_report_analysis_context(restored) == facts
    stored = report_primary_result(facts)["time_series"]
    assert all(isinstance(row["p_value"], str) and isinstance(row["value"], str) for row in stored["relations"])
    assert TimeSeriesResult.model_validate(stored) == result.time_series
    assert facts["results"][0]["raw_result"]["sha256"] == result.raw_result_sha256
    assert _report_graph_asset(state) is _report_graph_asset(restored) is None
    assert _report_execution_summary(state)["discovery_goal_status"] == "completed"
    prompt = _prompt_analysis_facts(facts)
    assert "raw_result" not in prompt["results"][0]
    assert "source_sha256" not in prompt["results"][0]["time_series"]["time_axis"]
    initial_report = _render(state)
    restored_report = _render(restored)
    assert initial_report["blocks"] == restored_report["blocks"]
    assert initial_report["assets"] == restored_report["assets"]
    overview = restored_report["assets"]["time_series_overview"]
    assert overview["metadata"]["purpose"] == "time_series_display_only"
    assert any(edge["source"] == "X|sensor" and edge["target"] == "Y"
               and {1, 3} <= set(edge["metadata"]["lag_steps"]) for edge in overview["edges"])
    assert any(edge["source"] == edge["target"] == "X|sensor" for edge in overview["edges"])
    block = _findings(restored_report)
    for lag in (1, 3):
        assert f"| X&#124;sensor | Y | {lag} | {lag * 300}.0 |" in block
    assert "| X&#124;sensor | X&#124;sensor | 1 | 300.0 |" in block
    assert "采样周期 300.0 秒" in block
    assert axis["end_time"] in block
    assert axis["source_sha256"] not in block
    recovered = recover_report(state, NodeError("report", TimeoutError("offline report timeout")))
    assert recovered["finalization_status"] == "valid"
    assert _findings(recovered["report_document"].model_dump(mode="json")) == block
    assert recovered["report_document"].assets["time_series_overview"].model_dump(mode="json") == overview


def test_gate_accepts_a_real_empty_pcmci_result():
    pytest.importorskip("tigramite")
    rng = np.random.default_rng(110)
    csv, axis = _input(pd.DataFrame(rng.normal(size=(240, 2)), columns=["X", "Y"]))
    params, identity = _identity(csv, axis)
    result, record = _execute(identity, "causal.pcmci_plus", csv,
                              TIME_SERIES_PARAMS | {"pc_alpha": 1e-6}, {"time_series": axis})
    assert result.status == "valid" and result.time_series.relations == ()
    with pytest.raises(StructuredResponseError) as error:
        _accept(params, identity, [(result, record)])
    assert error.value.rule == "no_valid_algorithm_with_valid_time_series"
    state = _accept(params, identity, [(result, record)], primary=result.result_ref)
    blocks = _render(state)["blocks"]
    assert "算法执行成功" in blocks[0]["content"]
    assert "未检出候选关系" in blocks[0]["content"]
    assert blocks == _render(_regeneration(state))["blocks"]


def test_static_success_does_not_hide_time_series_failure_or_missing_attempt(calculation):
    csv, axis = calculation
    params, identity = _identity(csv, axis)
    parameters = {"test": "fisherz"}
    artifacts = {"semantic_kinds": {"X|sensor": "continuous", "Y": "continuous", "Z": "continuous"}}
    static = _execute(identity, "causal.pc", csv, parameters, artifacts)
    assert static[0].status == "valid"
    static_params, static_identity = _identity(csv, axis, goal=None)
    static_execution = _execute(static_identity, "causal.pc", csv, parameters, artifacts)
    static_report = _accept(static_params, static_identity, [static_execution], primary=static_execution[0].result_ref)
    assert _report_graph_asset(static_report) is not None
    with pytest.raises(StructuredResponseError) as error:
        _accept(params, identity, [static], primary=static[0].result_ref)
    assert error.value.rule == "time_series_method_not_attempted"
    budget_limited = _accept(params, identity, [static], exhausted=True)
    assert _report_execution_summary(budget_limited)["discovery_goal_status"] == "incomplete"
    assert "时序目标未完成" in _findings(_render(budget_limited))
    assert _report_execution_summary(_accept(params, identity, [], exhausted=True))["discovery_goal_status"] == "incomplete"

    # Execute the real runner, then deliberately submit it for a different execution request.
    failed = _execute(identity, "causal.pcmci_plus", csv, TIME_SERIES_PARAMS, {"time_series": axis},
                      command_parameters=TIME_SERIES_PARAMS | {"tau_max": 2})
    assert failed[0].status == "execution_failed" and failed[0].time_series is None
    with pytest.raises(StructuredResponseError) as error:
        _accept(params, identity, [static, failed], primary=static[0].result_ref)
    assert error.value.rule == "time_series_primary_required"
    state = _accept(params, identity, [static, failed])
    assert _report_execution_summary(state)["algorithms"][0]["disposition"] == "supporting"
    assert _report_graph_asset(state) is None
    assert "时序目标未完成" in _findings(_render(_regeneration(state)))


def test_gate_checks_verified_window_and_current_lease(calculation):
    csv, axis = calculation
    params, identity = _identity(csv, axis)
    actual = _execute(identity, "causal.pcmci_plus", csv, TIME_SERIES_PARAMS, {"time_series": axis})
    different_axis = axis | {"selection_rule": "different-window-selection"}
    _, different = _identity(csv, different_axis)
    for trusted, rule in (
        (replace(identity, analysis_input=different.analysis_input), "time_series_result_invalid"),
        (replace(identity, lease_epoch=2), "assessment_ref_unknown_result"),
    ):
        with pytest.raises(StructuredResponseError) as error:
            _accept(params, trusted, [actual], primary=actual[0].result_ref)
        assert error.value.rule == rule
    with pytest.raises(ValidationError):
        _identity(csv, axis | {"sampling_period_seconds": 600})

    # A copied Pydantic model is not revalidated at construction. The Gate still checks the relation.
    result, record = actual
    series = result.time_series
    first, *rest = series.relations
    changed = first.model_copy(update={"lag_seconds": first.lag_seconds + 1})
    invalid = result.model_copy(update={"time_series": series.model_copy(update={"relations": (changed, *rest)})})
    with pytest.raises(StructuredResponseError) as error:
        _accept(params, identity, [(invalid, record)], primary=invalid.result_ref)
    assert error.value.rule == "time_series_result_invalid"


def test_child_prompt_uses_verified_axis_without_source_identity(calculation):
    csv, axis = calculation
    params, identity = _identity(csv, axis)
    child = to_deep_agent_input({"analysis_parameters": params, "route_decision": "data_processing"},
                                verified_time_axis=identity.analysis_input.time_series)
    content = " ".join(m["content"] for m in child["messages"] if isinstance(m, dict))
    assert "causal_pcmci_plus" in content
    assert '"sampling_period_seconds": 300.0' in content
    assert axis["source_sha256"] not in content


def test_real_uf_directions_and_relations_survive_regeneration():
    pytest.importorskip("tigramite")
    directory = Path(__file__).resolve().parents[3] / "杂项" / "dataset" / "industrial"
    path = next(directory.glob("sources/cipcad/*/UltraProcessed_Food/DatasetUF.csv"), None)
    if path is None:
        pytest.skip("local UF author dataset is not available in this checkout")
    frame = pd.read_csv(path, float_precision="round_trip")
    time = pd.to_datetime(frame.pop("DateTime"))
    groups = time.diff().ne(timedelta(minutes=5)).cumsum()
    rows = groups.index[groups == groups.value_counts(sort=False).idxmax()]
    csv, axis = _input(frame.loc[rows], source_bytes=path.read_bytes(), times=pd.DatetimeIndex(time.loc[rows]))
    axis["time_column"] = "DateTime"
    assert len(rows) == 1209
    params, identity = _identity(csv, axis)
    result, record = _execute(identity, "causal.pcmci_plus", csv, TIME_SERIES_PARAMS, {"time_series": axis})
    assert result.status == "valid"
    assert {relation.mark for relation in result.time_series.relations} == {"-->", "o-o", "x-x"}
    state = _accept(params, identity, [(result, record)], primary=result.result_ref)
    first = _render(state)
    restored = _render(_regeneration(state))
    assert first["blocks"] == restored["blocks"]
    assert first["assets"] == restored["assets"]
    overview = restored["assets"]["time_series_overview"]
    assert {edge["metadata"]["mark"] for edge in overview["edges"] if edge["edge_type"] == "undirected"} == {"o-o", "x-x"}
    table = _findings(restored)
    assert "同期未定向" in table and "同期方向冲突" in table
    rows_in_table = [line for line in table.splitlines() if line.startswith("|")][2:]
    assert len(rows_in_table) == len(result.time_series.relations)
    selected = next(relation for relation in result.time_series.relations if relation.target == "X17")
    assert any(f"| {selected.p_value} | {selected.value} |" in row for row in rows_in_table)
