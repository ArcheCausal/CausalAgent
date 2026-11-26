"""真实 StateGraph 与 InMemorySaver 的父子图暂停、恢复和 Job 清理。

直接调用生产 child 生命周期函数；不运行模型、沙盒或数据库读写。
"""

import asyncio
from typing import Any, TypedDict

import pytest
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from Agent.causal_agent.graph_utils import bind_node, bind_runnable_node, bind_subgraph_node
from Agent.data_processing.graph import (
    DATA_PROCESSING_CHECKPOINT_NAMESPACE,
    _specification_briefs,
    run_data_processing_child,
)
from Agent.deep_agent_tools.adapters import build_default_adapters
from Agent.deep_agent_tools.registry import build_default_registry
from Agent.deep_agent.context import AgentRunContext
from Agent.deep_agent_tools.interaction import DataProcessingQuestionRequest
from Agent.execution_control import JobExecutionRevoked
from app.agent.worker.execution_guard import JobExecutionGuard


JOB_ID = "00000000-0000-0000-0000-000000000901"
RUN_ID = "data-processing:" + "a" * 64
CHILD_NAMESPACE = f"{DATA_PROCESSING_CHECKPOINT_NAMESPACE}:{RUN_ID.partition(':')[2]}"


def test_processing_goal_uses_the_registered_algorithm_requirements() -> None:
    registry = build_default_registry(build_default_adapters(executor=None))
    briefs = _specification_briefs(registry)
    assert {item["capability_id"] for item in briefs} == {
        entry.spec.capability_id for entry in registry.entries
    }
    lingam = next(item for item in briefs if item["capability_id"] == "causal.direct_lingam")
    assert lingam["available"] is True
    assert lingam["requires_numeric_columns"] is True
    assert lingam["supports_encoded_categories"] is False


class ProcessingState(TypedDict, total=False):
    initialized: bool
    answers: list[Any]
    completed: bool
    revision: int
    needs_retry: bool


def _question(index: int) -> dict[str, Any]:
    return DataProcessingQuestionRequest(
        prompt=f"请确认第 {index} 个研究语义。",
        question_id=f"question-{index}",
        expires_at="2026-10-23T12:05:00Z",
    ).public_payload()


def _child_config(saver: InMemorySaver, *, namespace: str = CHILD_NAMESPACE) -> RunnableConfig:
    return {
        "configurable": {
            "thread_id": JOB_ID,
            "checkpoint_ns": namespace,
            "__pregel_checkpointer": saver,
        }
    }


def _graphs(
    saver: InMemorySaver,
    completed_steps: list[str],
    *,
    separate_question_nodes: bool,
    question_count: int = 3,
):
    def initialize(state: ProcessingState) -> dict[str, Any]:
        completed_steps.append("initialize")
        return {"initialized": True, "answers": [], "revision": 1}

    def finalize(state: ProcessingState) -> dict[str, Any]:
        completed_steps.append("finalize")
        return {"completed": True}

    child_builder = StateGraph(ProcessingState)
    child_builder.add_node("initialize", initialize)
    child_builder.add_edge(START, "initialize")
    previous = "initialize"
    if separate_question_nodes:
        for index in range(1, question_count + 1):
            def ask(state: ProcessingState, index: int = index) -> dict[str, Any]:
                answer = interrupt(_question(index))
                completed_steps.append(f"answered-{index}")
                return {"answers": [*state["answers"], answer]}

            name = f"ask_{index}"
            child_builder.add_node(name, ask)
            child_builder.add_edge(previous, name)
            previous = name
    else:
        def ask_all(state: ProcessingState) -> dict[str, Any]:
            answers = [interrupt(_question(index)) for index in range(1, question_count + 1)]
            completed_steps.append("answered-all")
            return {"answers": answers}

        child_builder.add_node("ask_all", ask_all)
        child_builder.add_edge(previous, "ask_all")
        previous = "ask_all"
    child_builder.add_node("finalize", finalize)
    child_builder.add_edge(previous, "finalize")
    child_builder.add_edge("finalize", END)
    child = child_builder.compile(checkpointer=saver)

    async def process(
        state: ProcessingState, *, runtime: Runtime, config: RunnableConfig
    ) -> dict[str, Any]:
        result = await run_data_processing_child(
            child, config=config, job_id=JOB_ID, run_id=RUN_ID,
            context=runtime.context,
            initial_input={"answers": []},
        )
        return dict(result)

    parent_builder = StateGraph(ProcessingState, context_schema=AgentRunContext)
    parent_builder.add_node(
        "processing", bind_subgraph_node(process, event_node_name="data_processing")
    )
    parent_builder.add_edge(START, "processing")
    parent_builder.add_edge("processing", END)
    return parent_builder.compile(checkpointer=saver), child


async def _invoke(
    parent, value: Any, config: RunnableConfig, events: list[dict],
    *, context: AgentRunContext | None = None,
) -> dict:
    async for mode, data in parent.astream(
        value, config, context=context or AgentRunContext(execution_guard=None),
        stream_mode=["updates", "custom"],
    ):
        if mode == "custom":
            events.append(data)
    snapshot = await parent.aget_state({"configurable": {"thread_id": JOB_ID}})
    result = dict(snapshot.values)
    if snapshot.interrupts:
        result["__interrupt__"] = snapshot.interrupts
    return result


@pytest.mark.parametrize("separate_question_nodes", [False, True])
@pytest.mark.parametrize("address_by_id", [False, True])
@pytest.mark.parametrize("explicit_parent_checkpoint", [False, True])
@pytest.mark.parametrize("rebuild_graphs", [False, True])
def test_multi_round_interrupts_do_not_reuse_the_first_answer_or_skip_questions(
    separate_question_nodes: bool, address_by_id: bool, explicit_parent_checkpoint: bool,
    rebuild_graphs: bool,
) -> None:
    async def scenario() -> None:
        saver = InMemorySaver()
        completed_steps: list[str] = []
        parent, child = _graphs(saver, completed_steps, separate_question_nodes=separate_question_nodes)
        root_config: RunnableConfig = {"configurable": {"thread_id": JOB_ID}}
        events: list[dict] = []
        result = await _invoke(parent, {"answers": []}, root_config, events)
        expected_answers = ["设备-日期", {"time_window": "day"}, "只统计有效告警"]
        seen_ids: list[str] = []

        for index, answer in enumerate(expected_answers, start=1):
            pending = result["__interrupt__"]
            assert len(pending) == 1
            assert pending[0].value == _question(index)
            if separate_question_nodes:
                assert pending[0].id not in seen_ids
            seen_ids.append(pending[0].id)
            assert not result.get("completed", False)
            assert "finalize" not in completed_steps

            child_snapshot = await child.aget_state(_child_config(saver))
            assert child_snapshot.interrupts[0].id == pending[0].id
            assert child_snapshot.values["initialized"] is True
            if separate_question_nodes:
                assert child_snapshot.values["answers"] == expected_answers[:index - 1]
            parent_snapshot = await parent.aget_state(root_config)
            assert parent_snapshot.next == ("processing",)
            resume_config = parent_snapshot.config if explicit_parent_checkpoint else root_config
            resume = {pending[0].id: answer} if address_by_id else answer
            if rebuild_graphs:
                parent, child = _graphs(
                    saver, completed_steps, separate_question_nodes=separate_question_nodes
                )
            result = await _invoke(parent, Command(resume=resume), resume_config, events)

            # Answer 1 must expose question 2, never complete or answer it.
            if index < len(expected_answers):
                assert result["__interrupt__"][0].value["question_id"] == f"question-{index + 1}"
                assert not result.get("completed", False)

        assert "__interrupt__" not in result
        assert result["completed"] is True
        assert result["answers"] == expected_answers
        assert completed_steps.count("initialize") == 1
        assert completed_steps.count("finalize") == 1
        assert sum(event["type"] == "node_attempt_start" for event in events) == 4
        assert all(event["type"] not in {"node_attempt_failed", "node_error"} for event in events)
        assert (await parent.aget_state(root_config)).next == ()
        child_snapshot = await child.aget_state(_child_config(saver))
        assert child_snapshot.values["answers"] == expected_answers
        assert child_snapshot.next == ()

        checkpoints = [item async for item in saver.alist(root_config)]
        namespaces = {item.config["configurable"]["checkpoint_ns"] for item in checkpoints}
        assert namespaces == {"", CHILD_NAMESPACE}
        assert all(item.config["configurable"]["thread_id"] == JOB_ID for item in checkpoints)
        await saver.adelete_thread(JOB_ID)
        assert [item async for item in saver.alist(root_config)] == []

    asyncio.run(scenario())


@pytest.mark.parametrize("wrapper", ["function", "runnable", "subgraph"])
@pytest.mark.parametrize("outcome", ["interrupt", "invalid_input", "revoked"])
def test_execution_wrappers_distinguish_interrupt_failure_and_revocation(
    wrapper: str, outcome: str
) -> None:
    async def scenario() -> None:
        async def work(state: ProcessingState) -> dict[str, Any]:
            if outcome == "invalid_input":
                return {"revision": int(state["answers"][0])}
            return {"answers": [interrupt(_question(1))]}

        if wrapper == "function":
            node = bind_node(work, event_node_name="data_processing")
        elif wrapper == "runnable":
            node = bind_runnable_node(RunnableLambda(work), event_node_name="data_processing")
        else:
            async def process(
                state: ProcessingState, *, runtime: Runtime, config: RunnableConfig
            ) -> dict[str, Any]:
                return await work(state)

            node = bind_subgraph_node(process, event_node_name="data_processing")

        saver = InMemorySaver()
        builder = StateGraph(ProcessingState, context_schema=AgentRunContext)
        builder.add_node("processing", node)
        builder.add_edge(START, "processing")
        builder.add_edge("processing", END)
        parent = builder.compile(checkpointer=saver)
        root_config: RunnableConfig = {"configurable": {"thread_id": JOB_ID}}
        events: list[dict] = []
        if outcome == "invalid_input":
            with pytest.raises(ValueError, match="invalid literal"):
                await _invoke(parent, {"answers": ["not-an-integer"]}, root_config, events)
            failed = [event for event in events if event["type"] == "node_attempt_failed"]
            assert len(failed) == 1
            assert failed[0]["error_kind"] == "ValueError"
        elif outcome == "revoked":
            guard = JobExecutionGuard(
                job_id=JOB_ID, worker_id="revoked-worker", attempt_count=1, lease_epoch=1,
            )
            guard.mark_revoked()
            with pytest.raises(JobExecutionRevoked):
                await _invoke(
                    parent, {}, root_config, events,
                    context=AgentRunContext(execution_guard=guard),
                )
            assert not events
        else:
            result = await _invoke(parent, {}, root_config, events)
            assert result["__interrupt__"][0].value == _question(1)
            result = await _invoke(parent, Command(resume="确认"), root_config, events)
            assert result["answers"] == ["确认"]
            assert "__interrupt__" not in result
            assert all(event["type"] not in {"node_attempt_failed", "node_error"} for event in events)

    asyncio.run(scenario())


def test_lifecycle_rejects_root_invocation_instead_of_silently_clearing_namespace() -> None:
    async def scenario() -> None:
        saver = InMemorySaver()
        _, child = _graphs(saver, [], separate_question_nodes=False)
        with pytest.raises(RuntimeError, match="inside a parent graph task"):
            await run_data_processing_child(
                child, config={"configurable": {"thread_id": JOB_ID}},
                job_id=JOB_ID, run_id=RUN_ID, context=AgentRunContext(execution_guard=None),
                initial_input={"answers": []},
            )
        assert [item async for item in saver.alist(None)] == []

    asyncio.run(scenario())


def test_validation_retry_starts_a_new_child_turn_and_waits_for_its_own_answer() -> None:
    async def scenario() -> None:
        saver = InMemorySaver()
        finished_revisions: list[int] = []

        def confirm(state: ProcessingState) -> dict[str, Any]:
            revision = state["revision"]
            answer = interrupt(_question(revision))
            finished_revisions.append(revision)
            return {"answers": [*state.get("answers", []), answer]}

        child_builder = StateGraph(ProcessingState)
        child_builder.add_node("confirm", confirm)
        child_builder.add_edge(START, "confirm")
        child_builder.add_edge("confirm", END)
        child = child_builder.compile(checkpointer=saver)

        async def process(
            state: ProcessingState, *, runtime: Runtime, config: RunnableConfig
        ) -> dict[str, Any]:
            result = await run_data_processing_child(
                child, config=config, job_id=JOB_ID, run_id=RUN_ID, context=runtime.context,
                initial_input={"revision": 1, "answers": []},
                retry_input={"revision": 2} if state.get("needs_retry") else None,
            )
            return dict(result)

        def validate(state: ProcessingState) -> dict[str, Any]:
            return {"needs_retry": state["revision"] == 1}

        parent_builder = StateGraph(ProcessingState, context_schema=AgentRunContext)
        parent_builder.add_node(
            "processing", bind_subgraph_node(process, event_node_name="data_processing")
        )
        parent_builder.add_node("validate", validate)
        parent_builder.add_edge(START, "processing")
        parent_builder.add_edge("processing", "validate")
        parent_builder.add_conditional_edges(
            "validate", lambda state: "processing" if state["needs_retry"] else END,
        )
        parent = parent_builder.compile(checkpointer=saver)
        root_config: RunnableConfig = {"configurable": {"thread_id": JOB_ID}}
        events: list[dict] = []
        result = await _invoke(parent, {}, root_config, events)
        assert result["__interrupt__"][0].value["question_id"] == "question-1"

        result = await _invoke(parent, Command(resume="首次确认"), root_config, events)
        assert result["__interrupt__"][0].value["question_id"] == "question-2"
        assert finished_revisions == [1]
        assert (await child.aget_state(_child_config(saver))).values["revision"] == 2

        result = await _invoke(parent, Command(resume="修订后确认"), root_config, events)
        assert "__interrupt__" not in result
        assert result["revision"] == 2
        assert result["answers"] == ["首次确认", "修订后确认"]
        assert finished_revisions == [1, 2]
        assert (await parent.aget_state(root_config)).next == ()
        assert all(event["type"] not in {"node_attempt_failed", "node_error"} for event in events)

    asyncio.run(scenario())
