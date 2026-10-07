"""验证 RAG 子图的私有 State、路由、降级和父图投影边界。"""

from __future__ import annotations

import asyncio
import os
import sys
import types
from types import SimpleNamespace
from typing import TypedDict

import pytest
from langchain_core.tools import tool
from langgraph.errors import NodeCancelledError
from langgraph.graph import END, StateGraph


for _key, _value in {
    "SECRET_KEY": "test-secret",
    "API_KEY": "test-api-key",
    "BASE_URL": "https://example.test",
    "MODEL": "test-model",
    "MYSQL_HOST": "mysql",
    "MYSQL_USER": "app",
    "MYSQL_PASSWORD": "password",
    "MYSQL_DATABASE": "causalagent",
}.items():
    os.environ.setdefault(_key, _value)


def _install_import_stubs():
    """隔离 RAG State 测试不需要的数据库和向量库调用。"""
    agent_connect = types.ModuleType("Database.agent_connect")
    agent_connect.require_frozen_file_for_job = lambda *args, **kwargs: None
    sys.modules.setdefault("Database.agent_connect", agent_connect)

_install_import_stubs()

from Agent.causal_agent import nodes, tool_subgraphs  # noqa: E402
from Agent.causal_agent.fault_tolerance import (  # noqa: E402
    degrade_rag_adapter_result,
    retry_transient_errors,
)
from Agent.causal_agent.graph_utils import bind_subgraph_node  # noqa: E402
from Agent.causal_agent.state import CausalAgentState  # noqa: E402
from Agent.causal_agent.tool_subgraphs import (  # noqa: E402
    build_rag_subgraph,
    route_rag_planner,
    route_rag_tool_result,
)
from Agent.llm_structured_output import StructuredOutputError  # noqa: E402
from app.agent.worker.event_adapter import LangGraphEventAdapter  # noqa: E402
from app.agent.worker.execution_guard import JobExecutionRevoked  # noqa: E402


def _rag_input() -> dict:
    """构造 RAG 子图需要的四个父图上下文。"""
    from langchain_core.messages import HumanMessage

    return {
        "messages": [HumanMessage(content="请补充因果推断方法的风险说明")],
        "analysis_parameters": {"target": "Y"},
        "preprocess_summary": "数据已经完成预处理。",
        "causal_analysis_result": {"success": True, "algorithm": "pc"},
    }


def _parent_input() -> dict:
    return {
        **_rag_input(),
        "username": "tester",
        "user_id": 1,
        "session_id": "session-1",
        "job_id": "job-1",
        "tool_call_request": False,
        "file_summary": None,
        "route_decision": "postprocess",
        "fold_decision": "preprocess",
        "postprocess_result": None,
        "chart_assets": None,
        "report_document": None,
    }


def _rag_tool(callback):
    """构造一个不访问真实知识库的异步 LangChain Tool。"""

    @tool
    async def rag_search(questions: list[dict], max_results: int = 5) -> dict:
        """执行测试用 RAG 查询。"""
        return await callback(questions, max_results)

    return rag_search


def _install_planner_questions(monkeypatch, *, questions=None, error=None):
    calls = []

    async def fake_questions(state, llm, max_questions):
        calls.append(max_questions)
        if error is not None:
            raise error
        return questions or [
            {
                "question": "PC 算法有哪些限制？",
                "intent": "补充方法风险",
                "priority": "high",
                "why_needed": "帮助报告说明解释边界",
            }
        ]

    monkeypatch.setattr(nodes, "get_rag_questions", fake_questions)
    return calls


def test_rag_routes_use_explicit_private_route_fields():
    """Planner/ToolNode 路由不再从展示消息猜测。"""
    assert route_rag_planner({"rag_route": "call_tool"}) == "call_tool"
    assert route_rag_planner({"rag_route": "finish"}) == "finish"
    assert route_rag_tool_result({"rag_route": "call_tool"}) == "parse"
    assert route_rag_tool_result({"rag_route": "finish"}) == "finish"


@pytest.mark.parametrize("rag_available, rag_tools", [(True, []), (False, [object()])])
def test_planner_preflight_skips_tool_when_rag_is_unavailable(
    monkeypatch,
    rag_available,
    rag_tools,
):
    """RAG 工具为空或启动检查失败时，Planner 预检直接进入 Finalize。"""
    question_calls = []

    async def should_not_generate_questions(*args, **kwargs):
        question_calls.append(True)
        raise AssertionError("RAG 不可用时不应生成问题")

    monkeypatch.setattr(nodes, "get_rag_questions", should_not_generate_questions)

    graph = build_rag_subgraph(object(), rag_tools, rag_available=rag_available)
    result = asyncio.run(graph.ainvoke(_rag_input()))

    assert question_calls == []
    assert result["rag_route"] == "finish"
    assert result["rag_status"] == "unavailable"
    assert result["rag_output"]["success"] is False
    assert result["rag_output"]["status"] == "unavailable"


def test_planner_success_enters_tool_and_preserves_single_question_status(monkeypatch):
    """Planner 成功进入 ToolNode，单问题 insufficient_evidence 语义不变。"""
    _install_planner_questions(monkeypatch)
    tool_calls = []

    async def search(questions, max_results):
        tool_calls.append((questions, max_results))
        return {
            "success": True,
            "questions": [
                {
                    "question": questions[0]["question"],
                    "status": "insufficient_evidence",
                    "answer": "根据当前检索到的证据，无法可靠回答该问题。",
                    "citations": [],
                    "retrieved_docs": [],
                }
            ],
            "evidence_count": 0,
            "summary": "知识库查询完成。",
        }

    graph = build_rag_subgraph(object(), [_rag_tool(search)])
    result = asyncio.run(graph.ainvoke(_rag_input()))

    assert len(tool_calls) == 1
    assert tool_calls[0][1] == 5
    assert result["rag_output"]["success"] is True
    assert result["rag_output"]["status"] == "available"
    assert result["rag_output"]["questions"][0]["status"] == "insufficient_evidence"
    assert result["rag_route"] == "finish"


def test_tool_success_false_still_runs_parser(monkeypatch):
    """ToolNode 返回 success=False 仍走 Parser，而不是直接跳过。"""
    _install_planner_questions(monkeypatch)
    parser_calls = []
    original_parser = nodes.rag_result_parser_node

    async def recording_parser(state):
        parser_calls.append(True)
        return await original_parser(state)

    monkeypatch.setattr(nodes, "rag_result_parser_node", recording_parser)

    async def search(questions, max_results):
        return {
            "success": False,
            "summary": "知识库查询失败。",
            "questions": [],
            "evidence_count": 0,
            "error": "检索服务不可用",
        }

    graph = build_rag_subgraph(object(), [_rag_tool(search)])
    result = asyncio.run(graph.ainvoke(_rag_input()))

    assert parser_calls == [True]
    assert "rag_output" in result, (list(result), result.get("rag_route"), result.get("rag_parse_result"))
    assert result["rag_output"]["success"] is False
    assert result["rag_output"]["status"] == "unavailable"
    assert result["rag_output"]["_tool_call"]["status"] == "error"


def test_query_failure_marker_is_unavailable_not_protocol_error(monkeypatch):
    """RAG 查询异常保留失败字典，但明确标记为查询错误而非协议错误。"""
    import importlib

    rag_query_task_module = importlib.import_module("Agent.tool_node.rag_query_task")

    def fail_query(questions):
        raise FileNotFoundError("知识库目录不存在")

    monkeypatch.setattr(rag_query_task_module, "get_rag_response", fail_query)

    async def scenario():
        class TaskState(TypedDict):
            result: object

        async def invoke_task(state: TaskState):
            return {"result": await rag_query_task_module.rag_query_task(["问题"])}

        graph = StateGraph(TaskState)
        graph.add_node("invoke_task", invoke_task)
        graph.set_entry_point("invoke_task")
        graph.add_edge("invoke_task", END)
        return await graph.compile().ainvoke({"result": None})

    result = asyncio.run(scenario())["result"]

    assert result["success"] is False
    assert result["status"] == "unavailable"
    assert result["error_type"] == "RAGQueryError"
    assert result["error_type"] != "ToolMessageProtocolError"


def test_invalid_json_tool_message_is_protocol_error_for_rag_parser():
    """非法 JSON 由 ToolMessage 解析器标记，并被 RAG Parser 归类为协议错误。"""
    from langchain_core.messages import AIMessage, ToolMessage

    state = {
        **_rag_input(),
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "rag_enrichment_search",
                        "args": {"questions": []},
                        "id": "rag-call-1",
                    }
                ],
            ),
            ToolMessage(
                content="not-json",
                tool_call_id="rag-call-1",
            ),
        ],
    }

    result = asyncio.run(nodes.rag_result_parser_node(state))

    assert result["rag_status"] == "protocol_error"
    assert result["rag_parse_result"]["status"] == "protocol_error"
    assert result["rag_parse_result"]["error_type"] == "ToolMessageProtocolError"


def test_mcp_parser_normalizes_shared_tool_message_protocol_error():
    """共享解析器的标记在 MCP Parser 中归一为既有 MCP 协议错误类型。"""
    from langchain_core.messages import AIMessage, ToolMessage

    state = {
        **_rag_input(),
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "causal_direct_lingam",
                        "args": {},
                        "id": "mcp-call-1",
                    }
                ],
            ),
            ToolMessage(
                content="not-json",
                tool_call_id="mcp-call-1",
            ),
        ],
    }

    result = asyncio.run(nodes.mcp_result_parser_node(state))

    assert result["causal_analysis_result"]["success"] is False
    assert result["causal_analysis_result"]["error_type"] == "MCPProtocolError"
    assert "ToolMessageProtocolError" not in result["causal_analysis_result"].values()


def test_planner_exception_retries_twice_then_finishes_without_tool(monkeypatch):
    """Planner 异常仍保留两次重试，并跳过 ToolNode 进入 Finalize。"""
    planner_calls = _install_planner_questions(monkeypatch, error=ConnectionError("planner failed"))
    tool_calls = []

    async def search(questions, max_results):
        tool_calls.append(True)
        return {"success": True, "questions": [], "evidence_count": 0}

    graph = build_rag_subgraph(object(), [_rag_tool(search)])
    result = asyncio.run(graph.ainvoke(_rag_input()))

    assert planner_calls == [3, 3]
    assert tool_calls == []
    assert "rag_output" in result, (list(result), result.get("rag_route"), result.get("rag_parse_result"))
    assert result["rag_output"]["success"] is False
    assert result["rag_output"]["status"] == "unavailable"


def test_toolnode_exception_retries_twice_skips_parser_and_finishes(monkeypatch):
    """ToolNode 自身异常保留两次重试，Parser 不执行，最终进入 Finalize。"""
    _install_planner_questions(monkeypatch)
    parser_calls = []

    async def parser_should_not_run(state):
        parser_calls.append(True)
        raise AssertionError("ToolNode 异常后不应进入 Parser")

    monkeypatch.setattr(nodes, "rag_result_parser_node", parser_should_not_run)
    tool_node_calls = []

    class FailingToolNode:
        def __init__(self, tools):
            self.tools = tools

        async def ainvoke(self, state, config):
            tool_node_calls.append(True)
            raise ConnectionError("tool node failed")

    monkeypatch.setattr(tool_subgraphs, "ToolNode", FailingToolNode)

    async def search(questions, max_results):
        return {"success": True, "questions": [], "evidence_count": 0}

    graph = build_rag_subgraph(object(), [_rag_tool(search)])
    result = asyncio.run(graph.ainvoke(_rag_input()))

    assert parser_calls == []
    assert tool_node_calls == [True, True]
    assert "rag_output" in result, (list(result), result.get("rag_route"), result.get("rag_parse_result"))
    assert result["rag_output"]["success"] is False
    assert result["rag_output"]["status"] == "unavailable"


def test_parser_exception_generates_protocol_degradation(monkeypatch):
    """Parser 异常通过 error handler 进入 Finalize，并标记 protocol_error。"""
    _install_planner_questions(monkeypatch)
    parser_calls = []

    async def failing_parser(state):
        parser_calls.append(True)
        raise ValueError("parser failed")

    monkeypatch.setattr(nodes, "rag_result_parser_node", failing_parser)

    async def search(questions, max_results):
        return {"success": True, "questions": [], "evidence_count": 0}

    graph = build_rag_subgraph(object(), [_rag_tool(search)])
    result = asyncio.run(graph.ainvoke(_rag_input()))

    assert parser_calls == [True]
    assert "rag_output" in result, (list(result), result.get("rag_route"), result.get("rag_parse_result"))
    assert result["rag_output"]["success"] is False
    assert result["rag_output"]["status"] == "protocol_error"


def test_parent_adapter_projects_only_knowledge_result_and_forwards_config_context():
    """父图只接收 knowledge_base_result，config/context 原样传入子图。"""
    observed = {}
    context = object()
    config = {"configurable": {"thread_id": "job-1", "marker": "kept"}}

    class FakeSubgraph:
        async def ainvoke(self, input_state, *, config, context):
            observed["input"] = input_state
            observed["config"] = config
            observed["context"] = context
            return {
                **input_state,
                "rag_route": "finish",
                "rag_questions": [{"question": "private"}],
                "rag_tool_message": "private tool message",
                "rag_parse_result": {"success": True},
                "rag_output": {"success": True, "status": "available", "questions": []},
            }

    async def scenario():
        parent = StateGraph(CausalAgentState)
        parent.add_node(
            "rag",
            bind_subgraph_node(
                nodes.rag_subgraph_adapter_node,
                event_node_name="rag",
                rag_subgraph=FakeSubgraph(),
            ),
        )
        parent.set_entry_point("rag")
        parent.add_edge("rag", END)
        return await parent.compile().ainvoke(
            _parent_input(),
            config=config,
            context=context,
        )

    result = asyncio.run(scenario())

    assert result["knowledge_base_result"]["status"] == "available"
    assert not {
        "rag_route",
        "rag_questions",
        "rag_tool_message",
        "rag_parse_result",
        "rag_status",
        "rag_output",
    }.intersection(result)
    assert set(observed["input"]) == {
        "messages",
        "analysis_parameters",
        "preprocess_summary",
        "causal_analysis_result",
    }
    assert observed["config"]["configurable"]["thread_id"] == "job-1"
    assert observed["context"] is context


def test_parent_adapter_keeps_nested_subgraph_events_visible():
    """父图使用 subgraphs=True 时仍能看到 RAG 子图 namespace 事件。"""
    async def planner_questions(state, llm, max_questions):
        return [
            {
                "question": "PC 算法有哪些限制？",
                "intent": "补充方法风险",
                "priority": "high",
                "why_needed": "帮助报告说明解释边界",
            }
        ]

    async def search(questions, max_results):
        return {"success": False, "summary": "不可用", "questions": [], "evidence_count": 0}

    original_questions = nodes.get_rag_questions
    nodes.get_rag_questions = planner_questions
    try:
        child = build_rag_subgraph(object(), [_rag_tool(search)])
        parent = StateGraph(CausalAgentState)
        parent.add_node(
            "rag",
            bind_subgraph_node(
                nodes.rag_subgraph_adapter_node,
                event_node_name="rag",
                rag_subgraph=child,
            ),
        )
        parent.set_entry_point("rag")
        parent.add_edge("rag", END)

        async def collect_events():
            events = []
            async for event in parent.compile().astream(
                _parent_input(),
                config={"configurable": {"thread_id": "job-1"}},
                stream_mode=["updates", "tasks"],
                subgraphs=True,
                version="v2",
            ):
                events.append(event)
            return events

        events = asyncio.run(collect_events())
    finally:
        nodes.get_rag_questions = original_questions

    nested_updates = [
        event
        for event in events
        if event.get("type") == "updates" and event.get("ns")
    ]
    nested_names = {
        name
        for event in nested_updates
        for name in event.get("data", {})
    }
    assert nested_updates
    assert {"rag_question_planner", "rag_tool_node", "rag_result_parser", "rag_finalize"} <= nested_names


def test_rag_adapter_failure_degrades_and_returns_to_agent():
    """适配节点异常只写入统一 RAG 结果，并回到父图 Agent。"""
    command = degrade_rag_adapter_result(
        {},
        SimpleNamespace(
            node="rag",
            error=ConnectionError("adapter failed"),
        ),
    )

    assert command.goto == "agent"
    assert set(command.update) == {"knowledge_base_result"}
    assert command.update["knowledge_base_result"]["success"] is False
    assert command.update["knowledge_base_result"]["status"] == "unavailable"


def test_rag_finalize_result_keeps_public_event_projection():
    """新 Finalize 输出仍被现有 RAG 阶段事件适配器识别。"""
    adapter = LangGraphEventAdapter("job-1", 1)
    adapter.convert({
        "type": "tasks",
        "ns": (),
        "data": {"id": "rag-task", "name": "rag", "input": {}},
    })
    events = adapter.convert({
        "type": "updates",
        "ns": ("rag:rag-task",),
        "data": {
            "rag_result_parser": {
                "rag_parse_result": {"success": False, "status": "unavailable"},
            }
        },
    })
    assert events == []

    events = adapter.convert({
        "type": "updates",
        "ns": ("rag:rag-task",),
        "data": {
            "rag_finalize": {
                "rag_output": {"success": False, "status": "unavailable"},
            }
        },
    })

    assert [event["type"] for event in events] == ["tool_call_result"]
    assert events[0]["summary"] == "暂不可用"


@pytest.mark.parametrize("cancel_exception", [JobExecutionRevoked("revoked"), asyncio.CancelledError()])
def test_rag_query_task_does_not_convert_cancellation(monkeypatch, cancel_exception):
    """RAG task 的内部异常捕获不能吞掉执行撤销或协程取消。"""
    import importlib

    rag_query_task_module = importlib.import_module("Agent.tool_node.rag_query_task")

    def fail_query(questions):
        raise cancel_exception

    monkeypatch.setattr(rag_query_task_module, "get_rag_response", fail_query)

    async def scenario():
        class TaskState(TypedDict):
            result: object

        async def invoke_task(state: TaskState):
            return {"result": await rag_query_task_module.rag_query_task(["问题"])}

        graph = StateGraph(TaskState)
        graph.add_node("invoke_task", invoke_task)
        graph.set_entry_point("invoke_task")
        graph.add_edge("invoke_task", END)
        if isinstance(cancel_exception, asyncio.CancelledError):
            with pytest.raises(NodeCancelledError) as error_info:
                await graph.compile().ainvoke({"result": None})
            assert isinstance(error_info.value.__cause__, asyncio.CancelledError)
        else:
            with pytest.raises(type(cancel_exception)):
                await graph.compile().ainvoke({"result": None})

    asyncio.run(scenario())


def test_rag_cancellation_is_not_retryable_or_degraded():
    """RAG 取消控制流不进入普通重试和降级结果。"""
    assert retry_transient_errors(JobExecutionRevoked("revoked")) is False
    assert retry_transient_errors(asyncio.CancelledError()) is False


def test_planner_structured_output_failure_uses_unified_degradation(monkeypatch):
    """Planner 保留现有 StructuredOutputError 捕获，但只生成私有降级结果。"""

    async def fail_questions(*args, **kwargs):
        raise StructuredOutputError(
            node_name="rag_question_planner",
            schema_name="RagQuestionBundle",
            cause=ValueError("invalid"),
        )

    monkeypatch.setattr(nodes, "get_rag_questions", fail_questions)
    result = asyncio.run(
        nodes.rag_question_planner_node(
            _rag_input(),
            object(),
            [_rag_tool(lambda questions, max_results: {})],
        )
    )

    assert result["rag_route"] == "finish"
    assert result["rag_status"] == "unavailable"
    assert result["rag_parse_result"]["status"] == "unavailable"
    assert "knowledge_base_result" not in result
