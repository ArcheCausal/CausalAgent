"""真实图循环与中断重放中的校验进度事件；不替代数据校验或数据库验收。"""

import asyncio
from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from Agent.data_processing.graph import _emit_validation_progress
from app.agent.public_events import public_event_payload
from app.agent.worker.event_adapter import LangGraphEventAdapter


JOB_ID = "3a31c230-28c5-4220-9668-7389cb7bf040"
CANDIDATE_ID = 8


class EventState(TypedDict):
    visits: int
    accepted: bool
    pause: bool


async def collect_validation_events(
    *, accepted: bool = False, resume_attempt: int | None = None,
) -> list[list[dict]]:
    """调用正式事件发布函数及适配器，只提供事件场景输入。"""
    job_attempt = 3

    async def publish(state: EventState, runtime: Runtime):
        _emit_validation_progress(
            runtime, job_attempt=job_attempt, dataset_id=CANDIDATE_ID,
            accepted=state["accepted"],
        )
        if state["pause"]:
            interrupt("resume event replay")
        return {"visits": state["visits"] + 1}

    workflow = StateGraph(EventState)
    workflow.add_node("validate_dataset", publish)
    workflow.add_edge(START, "validate_dataset")
    workflow.add_conditional_edges(
        "validate_dataset",
        lambda state: END if state["pause"] or state["visits"] == 2 else "validate_dataset",
    )
    graph = workflow.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": JOB_ID}}

    async def collect(graph_input):
        adapter = LangGraphEventAdapter(JOB_ID, job_attempt)
        events = []
        async for part in graph.astream(
            graph_input, config=config, stream_mode=["tasks", "custom"], version="v2",
        ):
            events.extend(
                event for event in adapter.convert(part) if event["type"] == "progress"
            )
        return events

    first = await collect({"visits": 0, "accepted": accepted, "pause": resume_attempt is not None})
    if resume_attempt is None:
        return [first]
    job_attempt = resume_attempt
    return [first, await collect(Command(resume="continue"))]


@pytest.mark.parametrize("accepted", [False, True])
def test_revisited_candidate_has_distinct_stage_and_event_keys(accepted):
    events, = asyncio.run(collect_validation_events(accepted=accepted))
    assert len(events) == 2
    assert len({event["step_id"] for event in events}) == 2
    assert len({event["_event_key"] for event in events}) == 2
    expected_summary = (
        "候选数据集通过独立校验。" if accepted
        else "候选数据集未通过独立校验，正在按检查结果修正。"
    )
    for event in events:
        assert event["summary"] == expected_summary
        assert len(event["_event_key"]) <= 255
        public = public_event_payload("progress", event)
        assert set(public) == {"type", "node_name", "title", "step_id", "summary"}
        assert JOB_ID not in str(public)
        assert event["_event_key"] not in str(public)


@pytest.mark.parametrize("resume_attempt", [3, 4])
def test_native_interrupt_replay_keeps_task_identity_and_separates_job_attempts(resume_attempt):
    first, resumed = asyncio.run(collect_validation_events(resume_attempt=resume_attempt))
    assert len(first) == len(resumed) == 1
    if resume_attempt == 3:
        assert first == resumed
    else:
        assert first[0]["step_id"] != resumed[0]["step_id"]
        assert first[0]["_event_key"] != resumed[0]["_event_key"]
