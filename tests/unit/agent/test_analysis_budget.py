"""直接检查预算状态转换与真实协议消息，不替代模型或算法执行。"""

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime

from Agent.deep_agent.analysis_budget import AnalysisBudgetMiddleware, CLOSEOUT_MODEL_CALL_LIMIT
from Agent.deep_agent.state import retry_deep_agent_from_checkpoint
from Agent.deep_agent.context import AgentRunContext, TrustedJobIdentity
from Agent.deep_agent_tools import build_default_registry
from Agent.deep_agent_tools.adapters import build_default_adapters
from Agent.deep_agent_tools.dependency_dispatch import build_algorithm_dependency_middleware
from Agent.deep_agent_tools.runtime_updates import RuntimeInvocationIdentity, build_pending_invocation
from tests.support.local_algorithm_execution import LocalAlgorithmExecutor


def test_exact_tool_limit_enters_closeout_before_next_model_call():
    middleware = AnalysisBudgetMiddleware(tool_limit=8, model_limit=12)
    state = {"thread_tool_call_count": {"__all__": 8}, "analysis_model_call_count": 4}
    assert middleware.before_model(state, None) == {
        "analysis_budget_exhausted": True, "analysis_budget_reason": "tool_calls",
    }


def test_model_limit_has_separate_closeout_allowance():
    middleware = AnalysisBudgetMiddleware(tool_limit=8, model_limit=1)
    state = {"analysis_model_call_count": 1}
    update = middleware.before_model(state, None)
    assert update["analysis_budget_reason"] == "model_calls"
    state.update(update, closeout_model_call_count=CLOSEOUT_MODEL_CALL_LIMIT)
    assert middleware.before_model(state, None)["jump_to"] == "end"


def test_overflow_batch_is_not_executed_and_returns_to_model_for_closeout():
    middleware = AnalysisBudgetMiddleware(tool_limit=1, model_limit=12)
    state = {"messages": [AIMessage(content="", tool_calls=[
        {"id": "a", "name": "causal_pc", "args": {}},
        {"id": "b", "name": "causal_cdfm", "args": {}},
    ])]}
    update = middleware.after_model(state, None)
    assert update["analysis_budget_exhausted"] is True
    assert update["jump_to"] == "model"
    assert update["thread_tool_call_count"] == {"__all__": 0}
    assert [m.tool_call_id for m in update["messages"] if isinstance(m, ToolMessage)] == ["b", "a"]


@pytest.mark.parametrize("receipt_status", ["success", "error"])
def test_structured_receipts_are_never_duplicated_in_closeout(receipt_status):
    middleware = AnalysisBudgetMiddleware(tool_limit=1, model_limit=12)
    messages = [AIMessage(content="", tool_calls=[
        {"id": "decision", "name": "FinalAnalysisDecision", "args": {}},
    ]), ToolMessage(content="validated", tool_call_id="decision", status=receipt_status)]
    state = {"analysis_budget_exhausted": True, "messages": messages}
    if receipt_status == "success":
        state["structured_response"] = {"outcome": "evidence_only"}
    update = middleware.after_model(state, None)
    assert "messages" not in update
    assert update["closeout_model_call_count"] == 1
    assert ("jump_to" in update) == (receipt_status == "error")


def test_closeout_blocks_new_calls_even_if_model_requests_a_hidden_tool():
    middleware = AnalysisBudgetMiddleware(tool_limit=8, model_limit=12)
    state = {"analysis_budget_exhausted": True, "messages": [AIMessage(content="", tool_calls=[
        {"id": "late", "name": "causal_g_formula", "args": {}},
    ])]}
    update = middleware.after_model(state, None)
    assert update["messages"][0].tool_call_id == "late"
    assert update["messages"][0].status == "error"
    assert update["structured_response"] is None
    assert update["jump_to"] == "model"


def test_gate_retry_preserves_exhausted_phase_and_closeout_count():
    state = {"analysis_budget_exhausted": True, "closeout_model_call_count": 1,
             "thread_tool_call_count": {"__all__": 8}, "messages": []}
    retry = retry_deep_agent_from_checkpoint(state, retry_instruction="仅修正结果引用。")
    assert retry["analysis_budget_exhausted"] is True
    assert retry["closeout_model_call_count"] == 1
    assert retry["thread_tool_call_count"] == {"__all__": 8}


def test_plain_text_closeout_gets_explicit_submission_instruction_within_allowance():
    middleware = AnalysisBudgetMiddleware(tool_limit=1, model_limit=12)
    state = {"analysis_budget_exhausted": True, "messages": [AIMessage(content="仅整理了结果。 ")]}
    update = middleware.after_model(state, None)
    assert update["jump_to"] == "model"
    assert isinstance(update["messages"][0], HumanMessage)
    state["closeout_model_call_count"] = CLOSEOUT_MODEL_CALL_LIMIT - 1
    exhausted = middleware.after_model(state, None)
    assert exhausted["jump_to"] == "end" and "messages" not in exhausted


def test_unexecuted_algorithm_is_recorded_as_budget_skip_without_scientific_checks():
    context = AgentRunContext(execution_guard=None, trusted_identity=TrustedJobIdentity(
        job_id="00000000-0000-0000-0000-000000000101",
        session_id="00000000-0000-0000-0000-000000000102", user_id=1,
        attempt_count=0, lease_epoch=1, worker_id="budget-check", input_identity="input-hash"))
    pending = build_pending_invocation(identity=RuntimeInvocationIdentity(
        provider_call_id="skipped-call", response_identity="response",
        response_identity_source="message_execution_id", provider_response_id=None,
        runtime_context=context), tool_name="causal_g_formula")
    executor = LocalAlgorithmExecutor()
    registry = build_default_registry(build_default_adapters(executor=executor))
    middleware = build_algorithm_dependency_middleware(registry=registry,
        max_parallel_tools_per_job=1, timeout_seconds=30)
    update = asyncio.run(middleware.aafter_agent({"analysis_budget_exhausted": True,
        "action_ledger": {pending.invocation_id: pending}}, Runtime(context=context)))
    record = update["action_ledger"][pending.invocation_id]
    result = update["algorithm_results"][record.result_ref]
    assert record.final_status == result.status == "skipped"
    assert result.diagnostics.safe_error_code == "ALGORITHM_BUDGET_EXHAUSTED"
    assert result.standardized_graph is None and result.ate_check is None
    assert record.effect_query is None
    assert executor.calls == []
