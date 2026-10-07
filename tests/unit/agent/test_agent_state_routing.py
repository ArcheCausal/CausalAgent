import asyncio
import json
import os
import sys
import types

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


for key, value in {
    "SECRET_KEY": "test-secret",
    "API_KEY": "test-api-key",
    "BASE_URL": "https://example.test",
    "MODEL": "test-model",
    "MYSQL_HOST": "mysql",
    "MYSQL_USER": "app",
    "MYSQL_PASSWORD": "password",
    "MYSQL_DATABASE": "causalagent",
}.items():
    os.environ.setdefault(key, value)


def _install_import_stubs():
    """隔离路由测试不需要的数据库和向量库依赖。"""
    agent_connect = types.ModuleType("Database.agent_connect")
    agent_connect.require_frozen_file_for_job = lambda *args, **kwargs: None
    sys.modules.setdefault("Database.agent_connect", agent_connect)

_install_import_stubs()

from Agent.causal_agent import edges, nodes
from Agent.causal_agent.fault_tolerance import (
    recover_fold_to_agent,
    recover_preprocess_to_agent,
    route_to_normal_chat,
)
from Agent.causal_agent.tool_subgraphs import (
    route_mcp_planner,
    route_mcp_tool_result,
    route_rag_planner,
)
from Agent.llm_structured_output import StructuredOutputError
from Agent.tool_node.mcp_tool_call_adapter import normalize_mcp_tool_call_message


def _state(message="普通问题", **updates):
    """构造 Agent/Fold 路由所需的最小状态。"""
    state = {
        "messages": [HumanMessage(content=message)],
        "user_id": 1,
        "username": "tester",
        "session_id": "session-1",
        "job_id": "job-1",
        "file_summary": {
            "user_file_id": 11,
            "object_id": 22,
            "file_hash": "a" * 64,
            "filename": "data.csv",
        },
    }
    state.update(updates)
    return state


def _mcp_tool(name="causal_pc"):
    """构造声明运行时身份字段的 MCP 工具测试替身。"""
    return types.SimpleNamespace(
        name=name,
        args={
            "csv_data": {},
            "target": {},
            "user_id": None,
            "job_id": None,
            "input_user_file_id": None,
            "input_object_id": None,
        },
    )


@pytest.mark.parametrize(
    "intent,expected_route",
    [
        ("normal_chat", "normal_chat"),
        ("start_analysis", "fold"),
        ("answer_report", "inquiry_answer"),
        ("switch_analysis_context", "context_switch"),
    ],
)
def test_agent_writes_every_legal_structured_intent(monkeypatch, intent, expected_route):
    """模型给出的每一种意图都由后端映射覆盖 checkpoint 中的旧决策值。"""
    async def fake_invoke(**kwargs):
        return nodes.AgentIntentDecision(
            intent=intent,
            context_hint=(
                "sales.csv" if intent == "switch_analysis_context" else None
            ),
        )

    monkeypatch.setattr(nodes, "ainvoke_structured", fake_invoke)
    result = asyncio.run(
        nodes.agent_node(
            _state(
                route_decision="postprocess",
                analysis_context={
                    "analysis_context_id": "ctx-1",
                    "latest_report_message_id": 3,
                },
            ),
            object(),
        )
    )

    assert result["route_decision"] == expected_route


def test_agent_deterministic_and_failure_routes_overwrite_old_state(monkeypatch):
    """明确因果请求、工具失败和结构化失败都显式写入保守路由。"""
    explicit = asyncio.run(
        nodes.agent_node(
            _state("请使用 data.csv 立即执行因果分析", route_decision="postprocess"),
            object(),
        )
    )
    tool_failure = asyncio.run(
        nodes.agent_node(
            _state(
                route_decision="fold",
                causal_analysis_result={"success": False, "error": "tool failed"},
            ),
            object(),
        )
    )

    async def fail_structured(**kwargs):
        raise StructuredOutputError(
            node_name="agent",
            schema_name="RouteQuery",
            cause=ValueError("invalid tool arguments"),
        )

    monkeypatch.setattr(nodes, "ainvoke_structured", fail_structured)
    structured_failure = asyncio.run(
        nodes.agent_node(
            _state(route_decision="fold"),
            object(),
        )
    )

    assert explicit["route_decision"] == "fold"
    assert tool_failure["route_decision"] == "normal_chat"
    assert structured_failure["route_decision"] == "normal_chat"


def test_mcp_protocol_failure_returns_to_agent_without_restarting_fold():
    """MCP 协议失败必须成为显式失败，使 Agent 结束本轮分析而不是重新加载文件。"""
    tool_call = AIMessage(
        content="",
        tool_calls=[{"name": "causal_pc", "args": {}, "id": "call-1"}],
    )
    tool_result = ToolMessage(
        content='{"result": {"unexpected": true}}',
        tool_call_id="call-1",
    )
    state = _state("请使用 data.csv 立即执行因果分析")
    state["messages"].extend([tool_call, tool_result])

    parsed = asyncio.run(nodes.mcp_result_parser_node(state))
    state.update(parsed)
    decision = asyncio.run(nodes.agent_node(state, object()))

    assert parsed["causal_analysis_result"]["success"] is False
    assert parsed["causal_analysis_result"]["error_type"] == "MCPProtocolError"
    assert decision["route_decision"] == "normal_chat"


def test_fold_extraction_failure_uses_frozen_file_and_can_validate(monkeypatch):
    """参数提取失败视为空值，仍使用 Job 冻结文件进行确定性校验。"""
    async def fail_structured(**kwargs):
        raise StructuredOutputError(
            node_name="fold",
            schema_name="foldQuery",
            cause=ValueError("invalid"),
        )

    monkeypatch.setattr(nodes, "ainvoke_structured", fail_structured)
    monkeypatch.setattr(
        nodes,
        "require_frozen_file_for_job",
        lambda *args: {"file_content": b"A,B\n1,2\n"},
    )
    monkeypatch.setattr(
        nodes,
        "get_data_summary",
        lambda df: {"n_rows": 2, "columns": ["A", "B"]},
    )
    monkeypatch.setattr(nodes, "validate_analysis", lambda *args, **kwargs: (1, [], []))

    result = asyncio.run(nodes.fold_node(_state(), object()))

    assert result["fold_decision"] == "preprocess"
    assert result["analysis_parameters"]["target"] is None
    assert result["analysis_parameters"]["treatment"] is None
    assert result["file_summary"]["rows"] == 2


@pytest.mark.parametrize("failure_stage", ["file", "validation"])
def test_fold_resume_paths_return_to_agent(monkeypatch, failure_stage):
    """文件错误恢复与参数补充恢复都写 fold_decision=agent。"""
    async def extracted(**kwargs):
        return nodes.foldQuery(target=None, treatment=None)

    monkeypatch.setattr(nodes, "ainvoke_structured", extracted)
    monkeypatch.setattr(nodes, "interrupt", lambda question: "用户补充信息")
    if failure_stage == "file":
        monkeypatch.setattr(
            nodes,
            "require_frozen_file_for_job",
            lambda *args: (_ for _ in ()).throw(FileNotFoundError("missing")),
        )
    else:
        monkeypatch.setattr(
            nodes,
            "require_frozen_file_for_job",
            lambda *args: {"file_content": b"A,B\n1,2\n"},
        )
        monkeypatch.setattr(nodes, "get_data_summary", lambda df: {"columns": ["A", "B"]})
        monkeypatch.setattr(
            nodes,
            "validate_analysis",
            lambda *args, **kwargs: (2, ["目标变量缺失"], []),
        )

    result = asyncio.run(nodes.fold_node(_state(), object()))

    assert result["fold_decision"] == "agent"
    assert "awaiting_input" not in result


@pytest.mark.parametrize("route", ["fold", "postprocess", "normal_chat", "inquiry_answer"])
def test_decision_router_reads_only_explicit_state(route):
    """中文展示消息不能覆盖显式 agent 路由。"""
    state = {"route_decision": route, "messages": [AIMessage(content="决策：信息不全，报告已获取")]}
    assert edges.decision_router(state) == route


@pytest.mark.parametrize("route", ["preprocess", "agent"])
def test_fold_router_reads_only_explicit_state(route):
    """中文展示消息不能覆盖显式 fold 路由。"""
    state = {"fold_decision": route, "messages": [AIMessage(content="信息完备，返回 agent")]}
    assert edges.fold_router(state) == route


def test_routers_use_safe_defaults_for_missing_or_invalid_state():
    """缺失或非法 checkpoint 字段走稳定保守分支。"""
    assert edges.decision_router({"messages": [AIMessage(content="信息完备")]}) == "normal_chat"
    assert edges.decision_router({"route_decision": "invalid", "messages": []}) == "normal_chat"
    assert edges.fold_router({"messages": [AIMessage(content="信息完备")]}) == "agent"
    assert edges.fold_router({"fold_decision": "invalid", "messages": []}) == "agent"


def test_mcp_subgraph_routes_only_valid_tool_calls_and_results():
    """MCP 子图仅执行有效调用，并在异常恢复结果出现后直接结束。"""
    planner_call = AIMessage(
        content="",
        tool_calls=[{"name": "causal_pc", "args": {}, "id": "call-1"}],
    )
    tool_result = ToolMessage(content='{"success": true}', tool_call_id="call-1")

    assert route_mcp_planner({"messages": [planner_call]}) == "tool"
    assert route_mcp_planner({"messages": [AIMessage(content="planner failed")]}) == "failed"
    assert route_mcp_tool_result(
        {
            "messages": [planner_call, tool_result],
            "causal_analysis_result": {"success": False},
        }
    ) == "parse"
    assert route_mcp_tool_result(
        {
            "messages": [planner_call, AIMessage(content="tool failed")],
            "causal_analysis_result": {"success": False},
        }
    ) == "failed"


def test_mcp_parser_preserves_subgraph_failure_without_tool_message():
    """ToolNode 异常恢复已生成的失败结果不能被 parser 的二次错误覆盖。"""
    failure = {
        "success": False,
        "error": "connection closed",
        "error_type": "ConnectionError",
    }

    result = asyncio.run(
        nodes.mcp_result_parser_node(
            {
                "messages": [AIMessage(content="MCP tool failed")],
                "causal_analysis_result": failure,
            }
        )
    )

    assert result["causal_analysis_result"] == failure
    assert result["tool_call_request"] is False


def test_recovery_handlers_keep_route_audit_fields_separate():
    """Agent、Fold、Preprocess 恢复只写各自拥有的路由审计字段。"""
    error = types.SimpleNamespace(node="test_node", error=RuntimeError("failed"))

    agent_command = route_to_normal_chat({}, error)
    fold_command = recover_fold_to_agent({}, error)
    preprocess_command = recover_preprocess_to_agent({}, error)

    assert agent_command.goto == "normal_chat"
    assert agent_command.update["route_decision"] == "normal_chat"
    assert fold_command.goto == "agent"
    assert fold_command.update["fold_decision"] == "agent"
    assert preprocess_command.goto == "agent"
    assert "fold_decision" not in preprocess_command.update


def test_rag_question_failure_skips_tool_node_with_stable_result(monkeypatch):
    """RAG 问题结构化失败不产生 tool_calls，并让报告流程可继续。"""
    async def fail_questions(*args, **kwargs):
        raise StructuredOutputError(
            node_name="rag_question_planner",
            schema_name="RagQuestionBundle",
            cause=ValueError("invalid"),
        )

    monkeypatch.setattr(nodes, "get_rag_questions", fail_questions)
    state = _state()
    result = asyncio.run(nodes.rag_question_planner_node(state, object(), []))

    assert result["rag_route"] == "finish"
    assert result["rag_parse_result"]["success"] is False
    assert result["rag_parse_result"]["questions"] == []
    assert route_rag_planner({**state, **result}) == "finish"


def test_mcp_adapter_preserves_standard_tool_calls_and_injects_runtime_data():
    """MCP planner 只注入可信 Job 身份，绝不把 CSV 正文放入 ToolMessage。"""
    tool = _mcp_tool()
    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "causal_pc",
                "args": {"target": "Y"},
                "id": "call-1",
                "type": "tool_call",
            }
        ],
    )

    normalized = normalize_mcp_tool_call_message(
        message,
        _state(),
        [tool],
    )

    assert isinstance(normalized, AIMessage)
    assert normalized.tool_calls[0]["name"] == "causal_pc"
    args = normalized.tool_calls[0]["args"]
    assert args["target"] == "Y"
    assert args["user_id"] == 1
    assert args["job_id"] == "job-1"
    assert args["input_user_file_id"] == 11
    assert args["input_object_id"] == 22
    assert "csv_data" not in args


def test_mcp_planner_disables_thinking_and_requires_a_tool_choice():
    """MCP planner 必须在协议层要求模型选择工具，而不是只依赖 Prompt。"""
    observed = {}

    class FakeBoundLLM:
        """返回可由 ToolNode 消费的标准工具调用消息。"""

        async def ainvoke(self, messages):
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "causal_pc",
                        "args": {"target": "Y"},
                        "id": "planner-call-1",
                        "type": "tool_call",
                    }
                ],
            )

    class FakeLLM:
        """记录 MCP planner 对模型副本和 bind_tools 的配置。"""

        extra_body = {"existing": "value"}

        def model_copy(self, *, update):
            observed["copy_update"] = update
            self.extra_body = update["extra_body"]
            return self

        def bind_tools(self, tools, **kwargs):
            observed["tools"] = tools
            observed["bind_kwargs"] = kwargs
            return FakeBoundLLM()

    tool = _mcp_tool()
    result = asyncio.run(
            nodes.mcp_planner_node(
                _state(
                    analysis_parameters={"target": "Y"},
                    preprocess_summary="数据已就绪",
                ),
            FakeLLM(),
            [tool],
        )
    )

    assert observed["copy_update"] == {
        "extra_body": {
            "existing": "value",
            "thinking": {"type": "disabled"},
        }
    }
    assert observed["bind_kwargs"] == {
        "tool_choice": "required",
        "parallel_tool_calls": False,
    }
    assert result["messages"][0].tool_calls[0]["name"] == "causal_pc"
    args = result["messages"][0].tool_calls[0]["args"]
    assert args["user_id"] == 1
    assert args["job_id"] == "job-1"
    assert args["input_user_file_id"] == 11
    assert args["input_object_id"] == 22
    assert "csv_data" not in args


def test_mcp_planner_deterministically_selects_explicit_direct_lingam():
    """用户明确点名 DirectLiNGAM 时必须优先选择独立工具并注入 CSV。"""

    class UnusedLLM:
        """如果确定性选择失效，测试会因访问模型而失败。"""

        extra_body = None

        def model_copy(self, *, update):
            raise AssertionError("明确 DirectLiNGAM 请求不应调用 LLM 选工具")

    tools = [_mcp_tool("causal_pc"), _mcp_tool("causal_direct_lingam")]
    result = asyncio.run(
        nodes.mcp_planner_node(
            _state(
                "请使用 DirectLiNGAM 分析这份 CSV",
            ),
            UnusedLLM(),
            tools,
        )
    )

    tool_call = result["messages"][0].tool_calls[0]
    assert tool_call["name"] == "causal_direct_lingam"
    assert tool_call["args"]["user_id"] == 1
    assert tool_call["args"]["job_id"] == "job-1"
    assert "csv_data" not in tool_call["args"]


def test_explicit_direct_lingam_mcp_stage_parses_and_routes_to_rag():
    """显式 DirectLiNGAM 请求应完成 MCP 阶段并进入后续 RAG/报告链。"""

    class UnusedLLM:
        """确定性工具选择不应访问模型。"""

        extra_body = None

        def model_copy(self, *, update):
            raise AssertionError("明确 DirectLiNGAM 请求不应调用 LLM 选工具")

    tools = [_mcp_tool("causal_direct_lingam")]
    state = _state(
        "请使用 DirectLiNGAM 分析这份 CSV",
    )
    planner_result = asyncio.run(
        nodes.mcp_planner_node(state, UnusedLLM(), tools)
    )
    planner_message = planner_result["messages"][0]
    tool_call = planner_message.tool_calls[0]
    tool_message = ToolMessage(
        content=json.dumps(
            {
                "success": True,
                "algorithm": "direct_lingam",
                "matrix_convention": "target_to_source",
                "data": {
                    "nodes": [{"id": "A"}, {"id": "B"}],
                    "edges": [{"from": "A", "to": "B", "arrows": "to", "weight": 0.8}],
                },
                "raw_results": {
                    "adjacency_matrix": [[0.0, 0.0], [0.8, 0.0]],
                    "causal_order": [0, 1],
                    "causal_order_names": ["A", "B"],
                },
            }
        ),
        tool_call_id=tool_call["id"],
    )

    parsed = asyncio.run(
        nodes.mcp_result_parser_node(
            {
                **state,
                "messages": state["messages"] + [planner_message, tool_message],
            }
        )
    )

    assert parsed["causal_analysis_result"]["success"] is True
    assert parsed["causal_analysis_result"]["algorithm"] == "direct_lingam"
    assert parsed["causal_analysis_result"]["_tool_call"]["name"] == "causal_direct_lingam"
    assert edges.mcp_router(parsed) == "rag"


def test_direct_lingam_report_context_includes_assumptions_and_versions():
    """报告上下文必须明确 DirectLiNGAM 的版本、参数、方向和解释边界。"""
    context = nodes._causal_method_context_for_report(
        {
            "success": True,
            "algorithm": "direct_lingam",
            "implementation": {
                "version": "0.1.4.7",
                "embedded_version": "1.5.4",
            },
            "parameters": {"measure": "pwling"},
            "matrix_convention": "target_to_source",
            "raw_results": {"causal_order_names": ["A", "B"]},
            "diagnostics": {"n_samples": 200, "n_features": 2},
        }
    )

    assert "causal-learn 0.1.4.7" in context
    assert "measure=pwling" in context
    assert "B[target, source]" in context
    assert "linear structural equation model" in context
    assert "non-Gaussian" in context
    assert "latent confounders" in context
    assert "candidate causal relations" in context


@pytest.mark.parametrize(
    "message",
    [
        AIMessage(content="no tool"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "unknown_tool",
                    "args": {},
                    "id": "call-2",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(
            content="",
            invalid_tool_calls=[
                {
                    "name": "causal_pc",
                    "args": "{invalid-json",
                    "id": "call-3",
                    "error": "invalid arguments",
                    "type": "invalid_tool_call",
                }
            ],
        ),
    ],
)
def test_mcp_adapter_rejects_missing_unknown_or_invalid_calls_without_fallback(message):
    """无调用、未知工具或非法参数必须明确失败，不能兜底选择第一项。"""
    tool = types.SimpleNamespace(name="causal_pc", args={})
    with pytest.raises(ValueError):
        normalize_mcp_tool_call_message(message, {}, [tool])
