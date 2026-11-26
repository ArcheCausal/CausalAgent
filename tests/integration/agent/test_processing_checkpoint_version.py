"""真实图与 saver 验证旧流程拒绝恢复，并保留原状态。"""
import asyncio
import pytest
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.runtime import Runtime
from Agent.causal_agent.state import CausalAgentState
from Agent.data_processing.parameters import PROCESSING_GRAPH_NAME, UnsupportedProcessingCheckpointError
from Agent.data_processing.graph import data_processing_parent_node
from Agent.deep_agent.context import AgentRunContext, TrustedJobIdentity
from app.agent.worker.graph_runner import ai_call_stream
from app.agent.worker.event_adapter import sanitize_public_error


@pytest.mark.parametrize("version", [None, 2])
def test_old_parent_checkpoint_is_rejected_and_not_rewritten(version):
    async def scenario():
        saver = InMemorySaver()
        builder = StateGraph(CausalAgentState)
        builder.add_node("agent", lambda state: {})
        builder.add_edge(START, "agent")
        builder.add_edge("agent", END)
        graph = builder.compile(checkpointer=saver, name=PROCESSING_GRAPH_NAME)
        job = "00000000-0000-0000-0000-000000000995"
        # Production graph configuration includes the same Job thread identity.
        from app.agent.worker.graph_runner import _graph_config
        config = _graph_config(user_id=1, session_id=job, job_id=job)
        state = {"data_processing_state_version": version} if version is not None else {"route_decision": "normal_chat"}
        await graph.aupdate_state(config, state, as_node="agent")
        before = await graph.aget_state(config)
        with pytest.raises(UnsupportedProcessingCheckpointError):
            _ = [event async for event in ai_call_stream("继续", 1, "tester", job,
                job_id=job, job_attempt=2, input_user_file_id=None, input_object_id=None,
                input_file_hash=None, input_filename=None, graph=graph, claim_kind="stale_recovery")]
        assert (await graph.aget_state(config)).values == before.values
        assert "重新发起" in sanitize_public_error(UnsupportedProcessingCheckpointError())
    asyncio.run(scenario())


def test_missing_frozen_input_returns_explicit_failure_before_database_or_model():
    async def scenario():
        context = AgentRunContext(execution_guard=None, trusted_identity=TrustedJobIdentity(
            job_id="00000000-0000-0000-0000-000000000995", session_id="00000000-0000-0000-0000-000000000996",
            user_id=1, attempt_count=1, lease_epoch=1, worker_id="missing-input", input_identity="a" * 64))
        result = await data_processing_parent_node({}, runtime=Runtime(context=context), config=None, agent=None, registry=None)
        assert result == {"data_processing_failure_code": "missing_frozen_input"}
    asyncio.run(scenario())
