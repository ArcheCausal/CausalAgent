"""真实 ToolNode、StateGraph 与 checkpoint 的方案状态传递；不调用外部服务。"""

import asyncio
from copy import deepcopy
import secrets

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from Agent.data_processing.agent import _plan_tool, _write_script_tool, ExecutionReportInput
from Agent.data_processing.graph import DataProcessingCompletionMiddleware, MAX_COMPLETION_RETRIES
from Agent.data_processing.parameters import ParameterContractError
from Agent.deep_agent.context import AgentRunContext, TrustedJobIdentity
from Agent.deep_agent.state import ProjectDeepAgentState, to_deep_agent_input
from Agent.deep_agent.graph import DeepAgentGraphConfig
from Agent.data_processing.agent import build_data_processing_agent
from tests.unit.agent.test_data_processing_lifecycle import JOB, PLAN, SOURCE, VERSION, current_state


def runtime():
    return Runtime(context=AgentRunContext(execution_guard=None, trusted_identity=TrustedJobIdentity(
        job_id=JOB, session_id="00000000-0000-0000-0000-000000000979", user_id=1,
        attempt_count=1, lease_epoch=1, worker_id="state-rule-test", input_identity=SOURCE,
    )))


def test_real_tools_preserve_identical_plan_then_invalidate_changed_script_and_plan():
    async def scenario():
        saver = InMemorySaver()
        def compile_tools():
            builder = StateGraph(ProjectDeepAgentState)
            builder.add_node("tools", ToolNode([_plan_tool(), _write_script_tool()], handle_tool_errors=False))
            builder.add_edge(START, "tools")
            builder.add_edge("tools", END)
            return builder.compile(checkpointer=saver)
        config = {"configurable": {"thread_id": JOB}}
        state = current_state()
        state["messages"] = [AIMessage(content="", tool_calls=[{
            "id": "same-plan", "name": "set_data_processing_plan", "args": deepcopy(PLAN)}])]
        result = await compile_tools().ainvoke(state, config)
        assert result["data_processing_confirmed_plan_version"] == VERSION
        assert result["data_processing_candidate_id"] == 12
        assert result["data_processing_execution_report"] == state["data_processing_execution_report"]
        # 重建图对象后读取同一个真实 saver，不能用初始默认值覆盖已有快照。
        result = await compile_tools().ainvoke({"messages": [AIMessage(content="", tool_calls=[{
            "id": "fix-script", "name": "write_processing_script", "args": {"script": "print('fix')"}}])]}, config)
        assert result["data_processing_confirmed_plan_version"] == VERSION
        assert result["data_processing_confirmed_plan"] == PLAN
        assert result["data_processing_candidate_id"] == 0
        assert result["data_processing_execution_report"] is None
        result = await compile_tools().ainvoke({"messages": [AIMessage(content="", tool_calls=[{
            "id": "change-plan", "name": "set_data_processing_plan",
            "args": {**PLAN, "summary": "改为每日聚合"}}])]}, config)
        assert result["data_processing_confirmed_plan_version"] is None
        assert result["data_processing_confirmed_plan"] is None
        assert result["data_processing_completion_retry_count"] == 1
    asyncio.run(scenario())


def test_completion_forces_original_confirmation_tool_without_resetting_budgets():
    state = current_state()
    state.update(data_processing_confirmed_plan=None, data_processing_confirmed_plan_version=None,
                 data_processing_candidate_id=0, data_processing_candidate_revision=0,
                 data_processing_execution_report=None, model_call_count=9, tool_call_count=11)
    result = asyncio.run(DataProcessingCompletionMiddleware().aafter_agent(state, runtime()))
    assert result["jump_to"] == "tools"
    assert result["messages"][0].tool_calls[0]["name"] == "request_data_processing_plan_confirmation"
    assert result["data_processing_completion_retry_count"] == 2
    assert not {"model_call_count", "tool_call_count", "data_processing_retry_count"}.intersection(result)
    state["data_processing_completion_retry_count"] = MAX_COMPLETION_RETRIES
    with pytest.raises(ParameterContractError, match="limit"):
        asyncio.run(DataProcessingCompletionMiddleware().aafter_agent(state, runtime()))


def test_parallel_tools_are_rejected_before_either_can_write_state():
    state = {"messages": [AIMessage(content="", tool_calls=[
        {"id": "plan", "name": "set_data_processing_plan", "args": PLAN},
        {"id": "run", "name": "execute_processing_script", "args": {}},
    ])]}
    gate = DataProcessingCompletionMiddleware()
    result = asyncio.run(gate.aafter_model(state, runtime()))
    assert result["jump_to"] == "model"
    assert all(isinstance(message, ToolMessage) and message.status == "error" for message in result["messages"])
    assert {message.tool_call_id for message in result["messages"]} == {"plan", "run"}
    state["messages"].extend(result["messages"])
    assert asyncio.run(gate.aafter_model(state, runtime())) is None


def test_execution_report_schema_cannot_accept_model_generated_file_facts():
    with pytest.raises(ValueError):
        ExecutionReportInput.model_validate({"explanation": "执行完成", "row_count": 123})


def test_parent_projection_exposes_report_separately_from_plan_metadata():
    report = current_state()["data_processing_execution_report"]
    result = to_deep_agent_input({"data_processing_execution_report": report})
    assert result["data_processing_execution_report"] == report
    assert "data_processing_metadata" not in result


def test_official_agent_compiles_completion_routes_and_retains_call_limits():
    # 只构建正式客户端和图，不发起请求，不提供模型输出。
    model = ChatOpenAI(model="gpt-4.1", api_key=secrets.token_urlsafe(32))
    graph = build_data_processing_agent(model=model, checkpointer=InMemorySaver(),
                                       config=DeepAgentGraphConfig(model_name="gpt-4.1", context_window_tokens=128000))
    topology = graph.get_graph()
    completion = "DataProcessingCompletionMiddleware.after_agent"
    assert completion in topology.nodes
    destinations = {edge.target for edge in topology.edges if edge.source == completion}
    assert "tools" in destinations and "__end__" in destinations
    assert any("ModelCallLimitMiddleware" in name for name in topology.nodes)
    tools = graph.nodes["tools"].bound.tools_by_name
    assert {"set_data_processing_plan", "save_data_processing_execution_report"}.issubset(tools)
    assert "describe_prepared_dataset" not in tools


@pytest.mark.parametrize("pending,ready", [(1, True), (None, False)])
def test_real_plan_tool_refuses_unprocessed_research_input(pending, ready):
    async def scenario():
        builder = StateGraph(ProjectDeepAgentState)
        builder.add_node("tools", ToolNode([_plan_tool()], handle_tool_errors=False))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        state = current_state()
        state.update(data_processing_pending_input_id=pending, data_processing_research_spec_ready=ready)
        state["messages"] = [AIMessage(content="", tool_calls=[{
            "id": "denied-plan", "name": "set_data_processing_plan", "args": {**PLAN, "summary": "不可提交"}}])]
        result = await builder.compile().ainvoke(state)
        assert result["data_processing_metadata"] == PLAN
        assert "set_research_spec" in result["messages"][-1].content
    asyncio.run(scenario())
