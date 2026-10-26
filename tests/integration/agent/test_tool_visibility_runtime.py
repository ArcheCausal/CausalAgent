"""真实框架请求序列化、本地算法失败和工具拒绝路径；不生成模型或搜索的替代返回。

HTTP 请求钩子在传输前显式中止，因此载荷检查不代表真实模型验收。
"""

import asyncio
from dataclasses import replace
from contextvars import ContextVar
import json

from deepagents.backends import StateBackend
import httpx
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.store.memory import InMemoryStore
import pytest

from Agent.deep_agent.context import AgentRunContext, TrustedJobIdentity
from Agent.deep_agent.finalization import FinalizationGate, StructuredResponseError
from Agent.deep_agent.graph import DeepAgentGraphConfig, build_deep_agent
from Agent.deep_agent.state import (
    ProjectDeepAgentState, initial_deep_agent_state, retry_deep_agent_from_checkpoint,
)
from Agent.deep_agent_tools.adapters import build_default_adapters
from Agent.deep_agent_tools.algorithm_tools import build_algorithm_tools
from Agent.deep_agent_tools.models import FinalAnalysisDecision
from Agent.deep_agent_tools.preprocessing import build_data_profile_from_csv
from Agent.deep_agent_tools.rag_evidence_tool import build_default_rag_evidence_tool
from Agent.deep_agent_tools.registry import build_default_registry
from Agent.deep_agent_tools.web_evidence_tool import build_default_web_evidence_tool
from config.secret_file import require_secret
from tests.support.bound_analysis_input import bind_analysis_input
from tests.support.local_algorithm_execution import (
    LocalAlgorithmExecutor, PC_CSV, PC_INSUFFICIENT_SAMPLE_CSV,
)


def _registry(csv_data=PC_CSV):
    executor = LocalAlgorithmExecutor(csv_data)
    return build_default_registry(build_default_adapters(executor=executor))


def _context(enabled, csv_data=PC_CSV):
    return AgentRunContext(
        execution_guard=None, web_search_enabled=enabled,
        trusted_identity=bind_analysis_input(TrustedJobIdentity(
            job_id="00000000-0000-0000-0000-000000000901",
            session_id="00000000-0000-0000-0000-000000000902",
            user_id=7, attempt_count=1, lease_epoch=2,
            worker_id="visibility-worker", input_identity="visibility-input",
        ), csv_data),
    )


def _input(messages, csv_data=PC_CSV):
    return initial_deep_agent_state(
        data_profile=build_data_profile_from_csv(csv_data),
        messages=messages, message_execution_id="visibility-response",
    )


class ModelRequestCaptured(Exception):
    """HTTP 请求已生成且尚未发送，停止图执行，不伪造模型响应。"""


def _was_captured(error):
    while isinstance(error, BaseException):
        if isinstance(error, ModelRequestCaptured):
            return True
        error = error.__cause__
    return False


def test_real_graph_serializes_isolated_concurrent_tools_and_retry_requests():
    async def run():
        observed = []
        current_flag = ContextVar("visibility_test_flag", default=False)

        async def capture(request):
            payload = json.loads(request.content)
            observed.append((current_flag.get(), {tool["name"] for tool in payload["tools"]}))
            raise ModelRequestCaptured()

        async with httpx.AsyncClient(event_hooks={"request": [capture]}) as client:
            model = ChatOpenAI(
                api_key=require_secret("DEEP_AGENT_API_KEY"),
                model="deepseek-v4-flash", use_responses_api=True,
                http_async_client=client, max_retries=0,
            )
            registry = _registry()
            graph = build_deep_agent(
                model=model,
                domain_tools=[
                    *build_algorithm_tools(registry),
                    build_default_rag_evidence_tool(),
                    build_default_web_evidence_tool(),
                ],
                backend=StateBackend(), store=InMemoryStore(), registry=registry,
                config=DeepAgentGraphConfig(context_window_tokens=1000000),
            )
            initial = _input([HumanMessage(content="检查当前工具。")])
            flags = [True, False, None, False, True]

            async def invoke(flag):
                token = current_flag.set(flag)
                try:
                    return await graph.ainvoke(initial, context=_context(flag))
                finally:
                    current_flag.reset(token)

            outcomes = await asyncio.gather(*(
                invoke(flag) for flag in flags
            ), return_exceptions=True)
            for outcome in outcomes:
                if isinstance(outcome, BaseException) and not _was_captured(outcome):
                    raise outcome
            assert all(_was_captured(outcome) for outcome in outcomes)
            assert len(observed) == len(flags)
            baseline = {
                "causal_pc", "causal_direct_lingam", "causal_cdfm",
                "rag_evidence_search", "read_file", "edit_file",
                "FinalAnalysisDecision",
            }
            assert all(baseline <= names for _, names in observed)
            assert all("request_data_processing" not in names for _, names in observed)
            assert all(("web_evidence_search" in names) is (flag is True)
                       for flag, names in observed)

            # 工具返回后的下一轮以及 Gate 修正输入都重新经过真实请求链。
            completed = await _invoke_tools(
                [build_default_web_evidence_tool().to_langchain_tool()],
                _context(False),
                [dict(name="web_evidence_search", args={"query": "causality"}, id="web-call")],
            )
            for state in (completed, retry_deep_agent_from_checkpoint(
                completed, retry_instruction="复用已有结果，并补齐校验要求的步骤。",
            )):
                before = len(observed)
                with pytest.raises(Exception) as stopped:
                    await graph.ainvoke(state, context=_context(False))
                assert _was_captured(stopped.value)
                assert len(observed) == before + 1
                assert "web_evidence_search" not in observed[-1][1]
                assert baseline <= observed[-1][1]
                assert "request_data_processing" not in observed[-1][1]
    asyncio.run(run())


async def _invoke_tools(tools, context, calls, *, csv_data=PC_CSV):
    builder = StateGraph(ProjectDeepAgentState, context_schema=AgentRunContext)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    return await builder.compile().ainvoke(
        _input([AIMessage(content="", tool_calls=calls)], csv_data), context=context,
    )


def test_disabled_production_tool_persists_real_denial_without_network():
    result = asyncio.run(_invoke_tools(
        [build_default_web_evidence_tool().to_langchain_tool()], _context(False),
        [dict(name="web_evidence_search", args={"query": "causality"}, id="disabled-web")],
    ))
    payload = json.loads(result["messages"][-1].content)
    assert payload["status"] == "disabled"
    assert payload["evidence"] == []
    record, = result["action_ledger"].values()
    assert record.final_status == "not_ready"
    assert record.attempts[0].safe_error_code == "WEB_SEARCH_DISABLED"


def test_gate_reuses_complete_same_attempt_but_requires_new_attempt_web_call():
    csv_data = PC_INSUFFICIENT_SAMPLE_CSV
    context = _context(True, csv_data)
    registry = _registry(csv_data)
    # 两行数据通过独立输入校验，但真实 PC runner 不能完成估计，返回 execution_failed。
    # 关闭 Web 的真实入口返回 terminal not_ready，没有执行搜索。
    state = asyncio.run(_invoke_tools(
        [
            *[tool.to_langchain_tool() for tool in build_algorithm_tools(registry)],
            build_default_web_evidence_tool().to_langchain_tool(),
        ], replace(context, web_search_enabled=False),
        [
            dict(name="causal_pc", args={}, id="pc-precheck"),
            dict(name="web_evidence_search", args={"query": "causality"}, id="web-precheck"),
        ],
        csv_data=csv_data,
    ))
    result, = state["algorithm_results"].values()
    assert result.status == "execution_failed"
    decision = FinalAnalysisDecision(
        outcome="evidence_only", primary_result_ref=None,
        result_assessments=[{
            "result_ref": result.result_ref, "disposition": "discarded",
            "rationale": "真实 PC runner 无法从两行数据完成估计。",
        }],
        conflict_status="none", selection_rationale="没有可用算法结果。",
        confidence="low", confidence_basis=["样本不足"],
    )
    gate = FinalizationGate(registry=registry)
    kwargs = dict(
        decision=decision, algorithm_results=state["algorithm_results"],
        action_ledger=state["action_ledger"], trusted_identity=context.trusted_identity,
        route_decision="fold", web_search_enabled=True,
    )
    assert gate.validate(**kwargs) == decision
    retry = retry_deep_agent_from_checkpoint(state, retry_instruction="修正最终决策。")
    assert gate.validate(**{**kwargs, "action_ledger": retry["action_ledger"]}) == decision
    web = next(record for record in state["action_ledger"].values()
               if record.tool_name == "web_evidence_search")
    next_context = replace(context, trusted_identity=replace(
        context.trusted_identity, attempt_count=2, lease_epoch=3, worker_id="next-worker",
    ))
    next_state = asyncio.run(_invoke_tools(
        [tool.to_langchain_tool() for tool in build_algorithm_tools(registry)],
        next_context, [dict(name="causal_pc", args={}, id="next-pc-precheck")],
        csv_data=csv_data,
    ))
    next_result, = next_state["algorithm_results"].values()
    assert next_result.status == "execution_failed"
    next_decision = FinalAnalysisDecision.model_validate({**decision.model_dump(), "result_assessments": [{
        "result_ref": next_result.result_ref, "disposition": "discarded",
        "rationale": "新 attempt 的真实 PC 估计失败。",
    }]})
    with pytest.raises(StructuredResponseError) as failure:
        gate.validate(**{
            **kwargs, "decision": next_decision,
            "algorithm_results": next_state["algorithm_results"],
            "trusted_identity": next_context.trusted_identity,
            "action_ledger": {**next_state["action_ledger"], web.invocation_id: web},
        })
    assert failure.value.rule == "analysis_route_without_web_invocation"

    # 不修改生产 Gate；未完成、终态不一致及缺少 attempts 均不能满足要求。
    for update in (
        {"final_status": "pending"}, {"attempts": {}},
        {"attempts": {0: web.attempts[0].model_copy(update={"status": "running"})}},
    ):
        with pytest.raises(StructuredResponseError):
            gate.validate(**{**kwargs, "action_ledger": {
                **state["action_ledger"], web.invocation_id: web.model_copy(update=update),
            }})
