"""真实 MySQL 绑定读取与真实父子 StateGraph/checkpoint 的显式验收。

复用隔离 database fixture；不调用模型、远端 MCP 或沙盒，不替换数据库 reader。
checkpoint 使用真实 MemorySaver，本文件不证明 PostgreSQL 持久化恢复。
所有生产数据库/父图模块均在 database fixture 初始化完成后才导入。
"""

import asyncio
from dataclasses import replace

import pytest

from tests.e2e.database.test_sandbox_sessions_mysql import database
from tests.support.mysql_bound_job import create_bound_job


def _graphs(observed, *, interrupt_after=()):
    from langchain_core.messages import HumanMessage
    from langchain_core.runnables import RunnableConfig
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.runtime import Runtime

    from Agent.causal_agent.graph import _deep_agent_parent_node
    from Agent.causal_agent.graph_utils import bind_subgraph_node
    from Agent.causal_agent.state import CausalAgentState
    from Agent.deep_agent.context import AgentRunContext
    from Agent.deep_agent.state import ProjectDeepAgentState
    from Agent.deep_agent_tools.adapters import build_default_adapters
    from Agent.deep_agent_tools.registry import build_default_registry
    from tests.support.local_algorithm_execution import LocalAlgorithmExecutor

    saver = MemorySaver()
    registry = build_default_registry(build_default_adapters(executor=LocalAlgorithmExecutor()))

    async def inspect_input(state: ProjectDeepAgentState, config: RunnableConfig, runtime: Runtime[AgentRunContext]):
        # 观察真实图的输入和上下文，不提供模型或算法的替代响应。
        observed.append({
            "node": "inspect", "state": dict(state),
            "configurable": dict(config["configurable"]), "context": runtime.context,
        })
        return {"data_profile": state["data_profile"]}

    async def checkpoint_message(state: ProjectDeepAgentState):
        observed.append({"node": "finish", "state": dict(state)})
        # 显式测试消息通过真实 reducer/checkpointer 保存，不代表模型输出。
        return {"messages": [HumanMessage(content="private child message", id="private-child-message")]}

    child_builder = StateGraph(ProjectDeepAgentState, context_schema=AgentRunContext)
    child_builder.add_node("inspect", inspect_input)
    child_builder.add_node("finish", checkpoint_message)
    child_builder.add_edge(START, "inspect")
    child_builder.add_edge("inspect", "finish")
    child_builder.add_edge("finish", END)
    child = child_builder.compile(checkpointer=saver, interrupt_after=list(interrupt_after))
    parent_builder = StateGraph(CausalAgentState, context_schema=AgentRunContext)
    parent_builder.add_node("deep_agent", bind_subgraph_node(
        _deep_agent_parent_node, event_node_name="deep_agent", deep_agent=child,
        store=None, memory_init_lock=asyncio.Lock(), registry=registry,
    ))
    parent_builder.add_edge(START, "deep_agent")
    parent_builder.add_edge("deep_agent", END)
    parent = parent_builder.compile(checkpointer=saver)
    return parent, child, saver, registry


def _child_config(identity):
    from Agent.deep_agent_tools.identity import build_deep_agent_run_id

    # 独立 child 图在自己的稳定 thread 根命名空间生成真实 checkpoint。
    return {"configurable": {
        "thread_id": build_deep_agent_run_id(job_id=identity.job_id),
        "checkpoint_ns": "",
    }}


def _parent_input(identity, *, message="parent message"):
    from langchain_core.messages import HumanMessage
    from Agent.data_processing.persistence import read_bound_dataset_identity
    from Agent.deep_agent_tools.preprocessing import build_data_profile_from_csv
    from tests.support.local_algorithm_execution import PC_CSV

    bound = read_bound_dataset_identity(identity)
    assert bound is not None
    return {
        "job_id": identity.job_id, "session_id": identity.session_id, "user_id": identity.user_id,
        "messages": [HumanMessage(content=message)],
        "analysis_parameters": bound["analysis_parameters"],
        "analysis_parameter_changes": bound["analysis_parameter_changes"],
        "analysis_context": bound["analysis_context"],
        "file_summary": {}, "data_profile": build_data_profile_from_csv(PC_CSV),
        "route_decision": "data_processing", "prepared_dataset_id": bound["dataset_id"],
        "prepared_dataset_hash": bound["data_hash"],
        "chart_assets": {"parent-only": {"description": "不传入 child 的父图字段"}},
    }


def _checkpoint_input(identity, registry):
    from langchain_core.messages import HumanMessage
    from Agent.deep_agent.state import initial_deep_agent_state
    from Agent.deep_agent_tools.identity import build_deep_agent_analysis_scope, build_deep_agent_run_id
    from Agent.deep_agent_tools.preprocessing import build_data_profile_from_csv
    from tests.support.local_algorithm_execution import PC_CSV

    state = initial_deep_agent_state(
        data_profile=build_data_profile_from_csv(PC_CSV),
        messages=[HumanMessage(content="seeded private child message", id="private-seed")],
        deep_agent_run_id=build_deep_agent_run_id(job_id=identity.job_id),
    )
    state["execution_scope"] = build_deep_agent_analysis_scope(
        job_id=identity.job_id, input_identity=identity.current_input_identity(),
        spec_digests=registry.spec_digests(),
    )
    return state


def test_parent_node_uses_stable_child_thread_id_and_minimal_projection(database):
    from Agent.deep_agent.context import AgentRunContext, AnalysisInputRef
    from Agent.deep_agent.state import assert_checkpoint_state_safe
    from Agent.deep_agent_tools.identity import build_deep_agent_run_id

    identity = create_bound_job(database)
    initial = _parent_input(identity)
    dataset_id = identity.analysis_input.dataset_id
    # worker 的空引用必须由父节点的真实 MySQL 读取恢复，而不是沿用 helper 的副本。
    identity = replace(identity, analysis_input=AnalysisInputRef())
    context = AgentRunContext(execution_guard=None, trusted_identity=identity, web_search_enabled=True)

    async def scenario():
        observed = []
        parent, child, saver, _registry = _graphs(observed)
        state = await parent.ainvoke(initial, {"configurable": {"thread_id": identity.job_id}}, context=context)
        expected = build_deep_agent_run_id(job_id=identity.job_id)
        assert state["deep_agent_run_id"] == expected
        assert state["deep_agent_status"] == "completed"
        assert identity.analysis_input.dataset_id == dataset_id
        assert identity.analysis_input.content_hash == initial["prepared_dataset_hash"]
        inspect, finish = observed
        assert inspect["node"] == "inspect" and finish["node"] == "finish"
        assert inspect["configurable"]["thread_id"] == expected
        assert inspect["context"].trusted_identity is identity
        child_state = inspect["state"]
        assert_checkpoint_state_safe(child_state)
        assert "chart_assets" not in child_state and "analysis_context" not in child_state
        assert all("本次运行已开启联网搜索" not in message.content for message in child_state["messages"])
        assert [message.content for message in state["messages"]] == ["parent message"]
        assert "deep_agent_messages" not in state
        snapshot = await child.aget_state(_child_config(identity))
        assert snapshot.values["deep_agent_run_id"] == expected and snapshot.next == ()
        assert snapshot.values["messages"][-1].content == "private child message"

    asyncio.run(scenario())


def test_parent_node_reads_matching_child_checkpoint_instead_of_projecting_messages(database):
    from Agent.deep_agent.context import AgentRunContext

    identity = create_bound_job(database)
    context = AgentRunContext(execution_guard=None, trusted_identity=identity)
    initial = _parent_input(identity, message="new parent message must not enter child")

    async def scenario():
        observed = []
        parent, child, saver, registry = _graphs(observed, interrupt_after=("inspect",))
        child_config = _child_config(identity)
        await child.ainvoke(_checkpoint_input(identity, registry), child_config, context=context)
        before = await child.aget_state(child_config)
        assert before.next == ("finish",)
        assert [item["node"] for item in observed] == ["inspect"]
        parent_checkpoint = await parent.aupdate_state(
            {"configurable": {"thread_id": identity.job_id}}, initial, as_node="__start__",
        )
        assert parent_checkpoint["configurable"]["checkpoint_id"]
        state = await parent.ainvoke(None, parent_checkpoint, context=context)
        # 若父节点重新投影并启动 child，inspect 会再次执行，且父消息会进入 child。
        assert [item["node"] for item in observed] == ["inspect", "finish"]
        resumed_messages = [message.content for message in observed[-1]["state"]["messages"]]
        assert resumed_messages == ["seeded private child message"]
        after = await child.aget_state(child_config)
        assert after.next == () and after.config != before.config
        assert state["deep_agent_status"] == "completed" and "deep_agent_messages" not in state
        assert [message.content for message in state["messages"]] == ["new parent message must not enter child"]

    asyncio.run(scenario())


def test_parent_retry_path_emits_public_progress_notices(database):
    from Agent.deep_agent.context import AgentRunContext
    from Agent.deep_agent_tools.identity import build_deep_agent_step_id

    identity = create_bound_job(database)
    context = AgentRunContext(execution_guard=None, trusted_identity=identity)
    instruction = "上一份结构化最终决策未通过程序事实校验。"
    initial = {**_parent_input(identity), "deep_agent_retry_instruction": instruction}

    async def scenario():
        observed, events = [], []
        parent, child, saver, registry = _graphs(observed)
        await child.ainvoke(_checkpoint_input(identity, registry), _child_config(identity), context=context)
        async for event in parent.astream(
            initial, {"configurable": {"thread_id": identity.job_id}},
            context=context, stream_mode="custom",
        ):
            events.append(event)
        assert [item["node"] for item in observed] == ["inspect", "finish", "inspect", "finish"]
        retry = observed[-2]
        messages = [message.content for message in retry["state"]["messages"]]
        assert messages[-1] == instruction
        assert "seeded private child message" in messages and "private child message" in messages
        assert "parent message" not in messages
        task, = [event for event in events if event.get("type") == "node_attempt_start"]
        step_id = build_deep_agent_step_id(
            job_id=identity.job_id, attempt_count=identity.attempt_count, task_id=task["task_id"],
        )
        notice, = [event for event in events if event.get("type") == "progress"]
        assert notice["node_name"] == "deep_agent" and notice["step_id"] == step_id
        assert retry["context"].deep_agent_step_id == step_id
        assert notice["_event_key"] == f"deep-agent-retry:{identity.attempt_count}:{step_id}"
        assert "复用已有结果，并补齐校验要求的步骤" in notice["summary"]
        assert "private child message" not in notice["summary"]
        assert instruction not in notice["summary"]

    asyncio.run(scenario())


def test_parent_node_rejects_checkpoint_dataset_mismatch(database):
    from Agent.deep_agent.context import AgentRunContext

    identity = create_bound_job(database)
    initial = _parent_input(identity)
    initial["prepared_dataset_id"] += 1

    async def scenario():
        observed = []
        parent, _child, _saver, _registry = _graphs(observed)
        with pytest.raises(RuntimeError, match="parent checkpoint dataset identity differs from MySQL binding"):
            await parent.ainvoke(
                initial, {"configurable": {"thread_id": identity.job_id}},
                context=AgentRunContext(execution_guard=None, trusted_identity=identity),
            )
        assert observed == []

    asyncio.run(scenario())
