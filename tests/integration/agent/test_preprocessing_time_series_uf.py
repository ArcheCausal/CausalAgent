"""Real UF offline integration: python -m pytest -m smoke tests/integration/agent/test_preprocessing_time_series_uf.py."""

from __future__ import annotations

import csv
import hashlib
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys

import pytest

from Agent.data_processing.contracts import ResearchPlanConfirmationRequest, ResearchPlanSummary
from Agent.data_processing.parameters import (
    PARAMETER_BINDING_KEY, ResearchSpec, commit_research_spec, parameter_binding,
    parameter_identity, research_parameters, restore_parameter_binding,
)
from Agent.data_processing.persistence import DatasetPersistenceError, _verified_time_series
from Agent.data_processing.plans import build_processing_plan_version, plan_confirmation_matches
from Agent.data_processing.time_axis import (
    TimeAxisError, TimeSeriesSelection, build_time_series_script, inspect_time_axis,
    prepare_time_series_plan,
)
from Agent.data_processing.validation import validate_candidate
from Agent.deep_agent.context import AnalysisInputRef
from Agent.deep_agent_tools.algorithm_specs import PCMCI_PLUS_SPEC, PC_SPEC
from Agent.deep_agent_tools.time_series import VerifiedTimeAxis
from app.agent.public_events import public_event_payload


pytestmark = pytest.mark.smoke  # The large official UF file is stored locally, outside Git.


@pytest.fixture(scope="module")
def uf_path() -> Path:
    root = Path(__file__).resolve().parents[3]
    files = list(root.glob("*/dataset/industrial/sources/cipcad/**/DatasetUF.csv"))
    assert len(files) == 1, "extract the official UF CSV into the repository's industrial sources directory"
    return files[0]


@pytest.fixture(scope="module")
def uf_axis(uf_path):
    return inspect_time_axis(uf_path.read_bytes(), time_column="DateTime")


def test_full_uf_diagnostics_and_pagination(uf_path, uf_axis):
    assert uf_axis["row_count"] == 23_132
    assert uf_axis["parse_failure_count"] == uf_axis["duplicate_count"] == uf_axis["reverse_interval_count"] == 0
    assert uf_axis["sampling_period_seconds"] == 300.0
    assert uf_axis["gap_count"] == 160
    assert uf_axis["longest_segment"]["row_count"] == 1_209
    assert uf_axis["segment_count"] > len(uf_axis["segments"])
    second_page = inspect_time_axis(uf_path.read_bytes(), time_column="DateTime", sampling_period_seconds=300.0,
                                    segment_offset=uf_axis["next_segment_offset"])
    assert second_page["segments"][0]["start_row"] > uf_axis["segments"][-1]["end_row"]


def test_uf_reconstruction_confirmation_and_admission(uf_path, uf_axis, tmp_path):
    source = uf_path.read_bytes()
    source_hash = hashlib.sha256(source).hexdigest()
    with uf_path.open(encoding="utf-8-sig", newline="") as stream:
        original = list(csv.reader(stream))
    # Check explicit rename and reordering as well as time-column exclusion.
    source_columns = ["X17", *original[0][1:-1]]
    columns = ["target_signal", *source_columns[1:]]
    question = "哪些指标的较早变化与 X17 的后续变化有关？"
    initial = research_parameters({"effect_request": None})
    parameters, history = commit_research_spec(
        initial, [], spec=ResearchSpec.model_validate({
            **initial, "target": "X17", "analysis_question": question, "discovery_goal": "time_series",
            "measurement_semantics": [{"scope": "columns", "columns": source_columns,
                                       "kind": "continuous", "evidence": "UF industrial benchmark measurement variables"}],
        }), input_columns=original[0], job_id="uf-job", source_input_id=1, expected_revision=0, reason="initial",
    )
    metadata = {
        "summary": "选取一段连续生产记录，按原顺序保留数值。",
        "observation_unit": "一条生产指标采样记录",
        "transformations": ["选取连续片段并保留原始指标值"],
        "variable_semantics": {column: "UF 测量指标" for column in columns},
        "analysis_columns": columns,
        "measurement_columns": {"DateTime": None, "X17": "target_signal"},
        "role_columns": {"target": "target_signal"},
    }
    candidate = tmp_path / "prepared.csv"

    def prepare(segment):
        selection = TimeSeriesSelection.model_validate({
            name: segment[name] for name in TimeSeriesSelection.model_fields
        })
        window = prepare_time_series_plan(source, selection=selection, metadata=metadata)
        script = build_time_series_script(window, metadata).encode()
        script_path = tmp_path / "processing.py"
        script_path.write_bytes(script)
        subprocess.run([sys.executable, str(script_path), str(uf_path), str(candidate)], check=True)
        return window, script, candidate.read_bytes()

    window, script, prepared = prepare(uf_axis["longest_segment"])
    assert window.row_count == 1_209
    wrong_semantics = {**metadata, "variable_semantics": {"DateTime": "原始时间列"}}
    with pytest.raises(TimeAxisError, match="字段语义只能描述 analysis_columns 内的指标"):
        prepare_time_series_plan(source, selection=TimeSeriesSelection.model_validate({
            key: getattr(window, key) for key in TimeSeriesSelection.model_fields
        }), metadata=wrong_semantics)
    selected = {**metadata, "time_series": window.model_dump(mode="json")}
    version = build_processing_plan_version(job_id="uf-job", source_hash=source_hash,
                                            semantic_identity="1" * 64, metadata=selected)
    confirmation = ResearchPlanConfirmationRequest(
        prompt="请确认连续采样片段。", question_id="uf-question", expires_at="2026-11-22T12:05:00Z",
        plan_version=version, plan=ResearchPlanSummary.model_validate({
            **selected, "effect_request": None, "discovery_goal": "time_series",
        }),
    ).public_payload()
    public = public_event_payload("interrupt", {"interaction": confirmation})["interaction"]
    assert public["plan"]["time_series"] == selected["time_series"]
    assert "source_sha256" not in public["plan"]["time_series"]
    assert plan_confirmation_matches(public, {"action": "confirm", "plan_version": version}) is True
    stored = {**selected, PARAMETER_BINDING_KEY: parameter_binding(parameters, history, job_id="uf-job")}

    def validate(data, *, specs=(PC_SPEC, PCMCI_PLUS_SPEC), plan=stored, processing_script=script,
                 source_bytes=source):
        return validate_candidate(
            data_bytes=data, script_bytes=processing_script, metadata=plan, analysis_parameters=parameters,
            specifications=specs, max_bytes=10_000_000, expected_parameter_identity=parameter_identity(parameters),
            source_bytes=source_bytes, source_hash=source_hash,
        )

    report = validate(prepared)
    assert report["accepted"], report["errors"]
    assert report["columns"] == columns
    assert report["target"] == "target_signal"
    assert report["compatible_algorithm_count"] == 1
    axis = VerifiedTimeAxis.model_validate(report["time_series"])
    assert axis.sampling_period_seconds == 300.0 and axis.row_count == 1_209 and axis.source_sha256 == source_hash
    restored_report = json.dumps(report)
    bound_axis = _verified_time_series(restored_report, json.loads(json.dumps(stored)), source_hash)
    ref = AnalysisInputRef.create(dataset_id=1, object_id=2, content_hash=hashlib.sha256(prepared).hexdigest(),
                                  filename="prepared.csv", semantic_kinds=report["semantic_kinds"], time_series=bound_axis)
    assert ref.time_series == axis
    with pytest.raises(DatasetPersistenceError):
        _verified_time_series({**report, "time_series": {**report["time_series"], "row_count": 1_208}}, stored, source_hash)
    row_ids = [original[0].index(name) for name in source_columns]
    output_rows = list(csv.reader(StringIO(prepared.decode(), newline="")))
    assert output_rows[1:] == [[row[index] for index in row_ids]
                               for row in original[window.start_row:window.end_row + 1]]
    static_only = validate(prepared, specs=(PC_SPEC,))
    assert not static_only["accepted"]
    assert [error["code"] for error in static_only["errors"]] == ["no_compatible_algorithm"]
    assert not validate(prepared, source_bytes=source + b"\n")["accepted"]

    # Preserve the row count while introducing an actual UF row after a gap.
    substitute = [original[window.end_row + 1][index] for index in row_ids]
    output_rows.pop(2)
    output_rows.append(substitute)
    tampered = StringIO(newline="")
    csv.writer(tampered).writerows(output_rows)
    rejected = validate(tampered.getvalue().encode())
    assert not rejected["accepted"] and "time_series" not in rejected
    assert any(error["code"] == "time_series_mismatch" for error in rejected["errors"])
    with pytest.raises(TimeAxisError, match="间隔中断"):
        prepare_time_series_plan(
            source, selection=TimeSeriesSelection(time_column="DateTime", start_row=window.end_row,
                                                  end_row=window.end_row + 1, sampling_period_seconds=300.0),
            metadata=metadata,
        )

    # A user can choose a different valid segment; the previous confirmation cannot authorize it.
    # Another UF segment with all 17 measurements varying; the 682-row segment has constant columns.
    alternate = next(item for item in uf_axis["segments"] if item["row_count"] == 488)
    alternate_window, alternate_script, alternate_data = prepare(alternate)
    alternate_plan = {**stored, "time_series": alternate_window.model_dump(mode="json")}
    alternate_report = validate(alternate_data, plan=alternate_plan, processing_script=alternate_script)
    assert alternate_report["accepted"], alternate_report["algorithm_compatibility"]
    new_version = build_processing_plan_version(
        job_id="uf-job", source_hash=source_hash, semantic_identity="1" * 64,
        metadata={**selected, "time_series": alternate_window.model_dump(mode="json")},
    )
    assert plan_confirmation_matches({**public, "plan_version": new_version},
                                    {"action": "confirm", "plan_version": version}) is False


def test_historical_static_identity_and_goal_revision():
    historical = {"effect_request": None, "target": None, "treatment": None, "analysis_question": "同期关系",
                  "measurement_semantics": [], "observation_unit": None, "time_window": "一个月", "aggregation_scope": None}
    old_hash = hashlib.sha256(json.dumps(historical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert parameter_identity(historical) == parameter_identity({**historical, "discovery_goal": "static"}) == old_hash
    old_binding = {"state_version": 4, "parameters": historical, "changes": [], "identity": old_hash, "revision": 0}
    assert restore_parameter_binding({PARAMETER_BINDING_KEY: old_binding}, job_id="uf-job")["analysis_parameters"] == historical
    params, history = historical, []
    for source_id, goal in enumerate(("time_series", "static", "time_series"), 1):
        params, history = commit_research_spec(
            params, history, spec=ResearchSpec.model_validate({**params, "discovery_goal": goal}),
            input_columns=["DateTime", "X17"], job_id="uf-job", source_input_id=source_id,
            expected_revision=source_id - 1, reason="user_change",
        )
        binding = parameter_binding(params, history, job_id="uf-job")
        restored = restore_parameter_binding({PARAMETER_BINDING_KEY: binding}, job_id="uf-job")
        assert restored["analysis_parameters"] == params
        assert restored["analysis_parameter_changes"] == history
        assert (binding["identity"] == old_hash) is (goal == "static")
