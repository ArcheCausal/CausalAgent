from datetime import datetime, timedelta

from app.chat.execution_phases import assemble_execution_phases


BASE = datetime(2026, 8, 11, 8, 0, 0)


def _message(message_id, message_type, *, job_id="job-1", input_id=None, source_event_id=None):
    return {
        "id": message_id,
        "message_type": message_type,
        "analysis_job_id": job_id,
        "analysis_job_input_id": input_id,
        "source_event_id": source_event_id,
    }


def _input(input_id, sequence, *, chat_message_id=None, seconds=0):
    return {
        "input_id": input_id,
        "job_id": "job-1",
        "sequence": sequence,
        "chat_message_id": chat_message_id if chat_message_id is not None else input_id,
        "created_at": BASE + timedelta(seconds=seconds),
    }


def _event(event_id, event_type, *, seconds, payload=None):
    return {
        "id": event_id,
        "job_id": "job-1",
        "event_type": event_type,
        "payload_json": payload or {"type": event_type},
        "created_at": BASE + timedelta(seconds=seconds),
    }


def test_multiple_interrupt_resume_phases_follow_input_sequence():
    messages = [
        _message(10, "user", input_id=1),
        _message(11, "ai", input_id=1, source_event_id=102),
        _message(12, "user", input_id=2),
        _message(13, "ai", input_id=2, source_event_id=105),
    ]
    events = [
        _event(100, "node_start", seconds=1, payload={"step_id": "a", "title": "分析"}),
        _event(101, "node_end", seconds=3, payload={"step_id": "a", "duration": 2, "status": "completed"}),
        _event(102, "interrupt", seconds=4),
        _event(103, "node_start", seconds=6, payload={"step_id": "b", "title": "继续"}),
        _event(104, "text_delta", seconds=7, payload={"delta": "hidden from history"}),
        _event(105, "final_result", seconds=9),
    ]

    phases = assemble_execution_phases(
        messages=messages,
        jobs=[{"job_id": "job-1", "status": "succeeded"}],
        inputs=[_input(1, 0, chat_message_id=10), _input(2, 1, chat_message_id=12, seconds=5)],
        events=events,
        snapshot_at=BASE + timedelta(seconds=10),
    )

    assert phases[10]["phase_sequence"] == 0
    assert phases[10]["status"] == "completed"
    assert [event["event_id"] for event in phases[10]["events"]] == [100, 101]
    assert phases[12]["phase_sequence"] == 1
    assert phases[12]["status"] == "completed"
    assert [event["event_id"] for event in phases[12]["events"]] == [103]
    assert "hidden from history" not in repr(phases)


def test_cancel_after_interrupt_updates_same_phase_without_duplicate():
    messages = [
        _message(10, "user", input_id=1),
        _message(11, "ai", input_id=1, source_event_id=102),
        _message(12, "ai", input_id=1, source_event_id=103),
    ]
    phases = assemble_execution_phases(
        messages=messages,
        jobs=[{"job_id": "job-1", "status": "canceled"}],
        inputs=[_input(1, 0, chat_message_id=10)],
        events=[
            _event(100, "node_start", seconds=1, payload={"step_id": "a", "attempt": 9}),
            _event(102, "interrupt", seconds=3),
            _event(103, "canceled", seconds=4),
        ],
        snapshot_at=BASE + timedelta(seconds=5),
    )

    assert list(phases) == [10]
    assert phases[10]["status"] == "canceled"
    assert phases[10]["last_event_id"] == 103
    assert "attempt" not in phases[10]["events"][0]


def test_current_interrupt_phase_stays_waiting_until_resume():
    phases = assemble_execution_phases(
        messages=[
            _message(10, "user", input_id=1),
            _message(11, "ai", input_id=1, source_event_id=102),
        ],
        jobs=[{"job_id": "job-1", "status": "waiting_input"}],
        inputs=[_input(1, 0, chat_message_id=10)],
        events=[
            _event(100, "node_start", seconds=1, payload={"step_id": "a"}),
            _event(102, "interrupt", seconds=3),
        ],
        snapshot_at=BASE + timedelta(seconds=4),
    )

    assert phases[10]["status"] == "waiting_input"


def test_active_phase_uses_latest_input_and_persisted_event_cursor():
    messages = [
        _message(10, "user", input_id=1),
        _message(11, "ai", input_id=1, source_event_id=102),
        _message(12, "user", input_id=2),
    ]
    phases = assemble_execution_phases(
        messages=messages,
        jobs=[{"job_id": "job-1", "status": "running"}],
        inputs=[_input(1, 0, chat_message_id=10), _input(2, 1, chat_message_id=12, seconds=5)],
        events=[
            _event(100, "node_start", seconds=1, payload={"step_id": "a"}),
            _event(102, "interrupt", seconds=3),
            _event(104, "node_start", seconds=6, payload={"step_id": "b"}),
        ],
        snapshot_at=BASE + timedelta(seconds=8),
    )

    assert phases[12]["status"] == "running"
    assert phases[12]["last_event_id"] == 104
    assert [event["event_id"] for event in phases[12]["events"]] == [104]


def test_active_queued_phase_is_returned_even_before_first_node_event():
    phases = assemble_execution_phases(
        messages=[_message(10, "user", input_id=1)],
        jobs=[{"job_id": "job-1", "status": "queued"}],
        inputs=[_input(1, 0, chat_message_id=10)],
        events=[],
        snapshot_at=BASE + timedelta(seconds=2),
    )

    assert phases[10]["status"] == "queued"
    assert phases[10]["last_event_id"] == 0
    assert phases[10]["events"] == []


def test_missing_boundary_message_drops_ambiguous_segment():
    phases = assemble_execution_phases(
        messages=[_message(10, "user", input_id=1)],
        jobs=[{"job_id": "job-1", "status": "succeeded"}],
        inputs=[_input(1, 0, chat_message_id=10)],
        events=[
            _event(100, "node_start", seconds=1, payload={"step_id": "a"}),
            _event(101, "final_result", seconds=2),
        ],
        snapshot_at=BASE + timedelta(seconds=3),
    )

    assert phases == {}


def test_error_boundary_marks_phase_failed_and_keeps_public_nodes():
    phases = assemble_execution_phases(
        messages=[
            _message(10, "user", input_id=1),
            _message(11, "ai", input_id=1, source_event_id=102),
        ],
        jobs=[{"job_id": "job-1", "status": "failed"}],
        inputs=[_input(1, 0, chat_message_id=10)],
        events=[
            _event(100, "node_start", seconds=1, payload={"step_id": "a"}),
            _event(102, "error", seconds=2, payload={"message": "任务失败"}),
        ],
        snapshot_at=BASE + timedelta(seconds=3),
    )

    assert phases[10]["status"] == "failed"
    assert [event["event_id"] for event in phases[10]["events"]] == [100]


def test_completed_deep_agent_phase_replays_tool_and_public_decision_events():
    phases = assemble_execution_phases(
        messages=[
            _message(10, "user", input_id=1),
            _message(11, "ai", input_id=1, source_event_id=106),
        ],
        jobs=[{"job_id": "job-1", "status": "succeeded"}],
        inputs=[_input(1, 0, chat_message_id=10)],
        events=[
            _event(100, "node_start", seconds=1, payload={
                "step_id": "deep-step", "node_name": "deep_agent",
            }),
            _event(101, "decision", seconds=2, payload={
                "step_id": "deep-step", "node_name": "deep_agent",
                "decision_kind": "algorithm", "tool_name": "causal_pc",
                "summary": "连续数据适合使用 PC。", "private": "hidden",
            }),
            _event(102, "tool_call_start", seconds=3, payload={
                "step_id": "deep-step", "node_name": "deep_agent",
                "tool_name": "causal_pc", "argument_keys": ["alpha"],
            }),
            _event(103, "tool_call_result", seconds=4, payload={
                "step_id": "deep-step", "node_name": "deep_agent",
                "tool_name": "causal_pc", "summary": "调用完成",
                "status": "succeeded",
            }),
            _event(104, "node_end", seconds=5, payload={
                "step_id": "deep-step", "node_name": "deep_agent",
                "duration": 4, "status": "completed",
            }),
            _event(106, "final_result", seconds=6),
        ],
        snapshot_at=BASE + timedelta(seconds=7),
    )

    events = phases[10]["events"]
    assert [event["event_id"] for event in events] == [100, 101, 102, 103, 104]
    assert events[1]["decision_kind"] == "algorithm"
    assert events[1]["summary"] == "连续数据适合使用 PC。"
    assert "private" not in repr(events)


def test_legacy_messages_without_job_links_remain_outside_phases():
    phases = assemble_execution_phases(
        messages=[{
            "id": 1,
            "message_type": "user",
            "analysis_job_id": None,
            "analysis_job_input_id": None,
            "source_event_id": None,
        }],
        jobs=[],
        inputs=[],
        events=[],
        snapshot_at=BASE,
    )

    assert phases == {}
