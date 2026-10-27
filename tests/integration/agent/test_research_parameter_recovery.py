"""真实 LangGraph/InMemorySaver 与参数纯函数协作；不运行模型、数据库或沙盒。"""

import asyncio
from typing import Any

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from Agent.data_processing.graph import DATA_PROCESSING_CHECKPOINT_NAMESPACE, run_data_processing_child
from Agent.data_processing.parameters import PROCESSING_STATE_VERSION, ParameterContractError, apply_parameter_patch
from Agent.deep_agent.context import AgentRunContext
from Agent.deep_agent.state import ProjectDeepAgentState
from Agent.deep_agent_tools.identity import build_data_processing_run_id
from Agent.deep_agent_tools.interaction import DataProcessingQuestionRequest


JOB = "00000000-0000-0000-0000-000000000951"
RUN = build_data_processing_run_id(job_id=JOB, source_input_identity="a" * 64)
BASE = {"target": "outcome", "treatment": None, "analysis_question": "研究关系", "measurement_semantics": []}


def child_config(saver, namespace=None):
    return {"configurable": {"thread_id": JOB, "__pregel_checkpointer": saver,
                             "checkpoint_ns": namespace or f"{DATA_PROCESSING_CHECKPOINT_NAMESPACE}:{RUN.partition(':')[2]}"}}


def initial():
    return {"analysis_parameters": dict(BASE), "analysis_parameter_changes": [],
            "data_processing_state_version": PROCESSING_STATE_VERSION, "data_processing_run_id": RUN,
            "messages": [HumanMessage(content="开始")]}


def graphs(saver, *, retry=False, retry_update=None):
    builder = StateGraph(ProjectDeepAgentState)
    for index, patch in ((1, {"target": None}), (2, {"time_window": "每天"})):
        def revise(state, index=index, patch=patch):
            if len(state["analysis_parameter_changes"]) >= index:
                return {}
            request = DataProcessingQuestionRequest(
                prompt=f"确认第{index}项研究参数", question_id=f"q-{index}", expires_at="2026-10-25T12:05:00Z",
            )
            answer = interrupt(request.public_payload())
            assert answer == f"answer-{index}"
            parameters, history = apply_parameter_patch(
                state["analysis_parameters"], state["analysis_parameter_changes"], patch=patch,
                job_id=JOB, source_input_id=100 + index,
            )
            return {"analysis_parameters": parameters, "analysis_parameter_changes": history}
        builder.add_node(f"question_{index}", revise)
    builder.add_edge(START, "question_1")
    builder.add_edge("question_1", "question_2")
    builder.add_edge("question_2", END)
    child = builder.compile(checkpointer=saver)

    async def process(state: ProjectDeepAgentState, runtime: Runtime, config: RunnableConfig) -> dict[str, Any]:
        return dict(await run_data_processing_child(
            child, config=config, job_id=JOB, run_id=RUN, context=runtime.context,
            initial_input=initial(), retry_input=(retry_update or {"messages": [HumanMessage(content="修正脚本")]}) if retry else None,
        ))

    parent_builder = StateGraph(ProjectDeepAgentState, context_schema=AgentRunContext)
    parent_builder.add_node("processing", process)
    parent_builder.add_edge(START, "processing")
    parent_builder.add_edge("processing", END)
    return parent_builder.compile(checkpointer=saver), child


def test_two_revisions_survive_worker_graph_rebuild_retry_and_parent_replay():
    async def scenario():
        saver = InMemorySaver()
        parent, child = graphs(saver)
        config = {"configurable": {"thread_id": JOB}}
        context = AgentRunContext(execution_guard=None)
        result = await parent.ainvoke(initial(), config, context=context)
        assert result["__interrupt__"][0].value["question_id"] == "q-1"
        result = await parent.ainvoke(Command(resume="answer-1"), config, context=context)
        assert result["__interrupt__"][0].value["question_id"] == "q-2"
        saved = await child.aget_state(child_config(saver))
        assert saved.values["analysis_parameters"]["target"] is None
        parent, child = graphs(saver)
        result = await parent.ainvoke(Command(resume="answer-2"), config, context=context)
        expected_parameters = result["analysis_parameters"]
        expected_history = result["analysis_parameter_changes"]
        assert expected_parameters["time_window"] == "每天"
        assert [row["revision"] for row in expected_history] == [1, 2]
        for retry in (False, True):
            parent, child = graphs(saver, retry=retry)
            result = await parent.ainvoke({"messages": [HumanMessage(content="父节点重放")]}, config, context=context)
            assert result["analysis_parameters"] == expected_parameters
            assert result["analysis_parameter_changes"] == expected_history
        parent, child = graphs(saver, retry=True, retry_update={"analysis_parameters": BASE})
        with pytest.raises(ParameterContractError, match="cannot overwrite"):
            await parent.ainvoke({}, config, context=context)
    asyncio.run(scenario())


@pytest.mark.parametrize("namespace", [None, "data_processing_v1:old-goal-dependent-id"])
def test_legacy_checkpoint_is_rejected_without_creating_a_replacement(namespace):
    async def scenario():
        saver = InMemorySaver()
        parent, child = graphs(saver)
        await child.aupdate_state(child_config(saver, namespace), {"analysis_parameters": BASE})
        count_before = len([row async for row in saver.alist({"configurable": {"thread_id": JOB}})
                            if row.config["configurable"].get("checkpoint_ns")])
        with pytest.raises(ParameterContractError, match="legacy"):
            await parent.ainvoke(initial(), {"configurable": {"thread_id": JOB}},
                                 context=AgentRunContext(execution_guard=None))
        assert count_before == len([row async for row in saver.alist({"configurable": {"thread_id": JOB}})
                                    if row.config["configurable"].get("checkpoint_ns")])
    asyncio.run(scenario())
