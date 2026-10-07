"""P0 Deep Agents 0.7.13 and parent LangGraph checkpoint spike.

The real-model checks use only structural assertions. No prompt, response text,
provider ID, token, or credential is printed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any, ClassVar, TypedDict

from deepagents import (
    DeepAgentState,
    FilesystemMiddleware,
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.middleware.summarization import create_summarization_middleware
from deepagents.backends import StateBackend
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.errors import NodeCancelledError
from langgraph.graph import END, START, StateGraph

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Agent.deep_agent_tools.models import FinalAnalysisDecision
from app.agent.worker.execution_guard import JobExecutionRevoked


MODEL_KEY = "openai:deepseek-v4-flash"
ALLOWED_FILESYSTEM_TOOLS = {"read_file", "edit_file"}


class RuntimeContext(TypedDict):
    runtime_marker: str


class SpikeState(DeepAgentState):
    marker: str
    files: dict[str, str]
    structured_response: FinalAnalysisDecision | None


class BindableFakeChatModel(FakeMessagesListChatModel):
    """Fake model that accepts the tool binding required by Deep Agents."""

    bound_tool_names: ClassVar[list[list[str]]] = []

    @property
    def model_name(self) -> str:
        return "deepseek-v4-flash"

    def _get_ls_params(self, **kwargs: Any) -> dict[str, str]:
        return {"ls_provider": "openai", "ls_model_name": self.model_name}

    def bind_tools(self, tools: Any, **kwargs: Any) -> "BindableFakeChatModel":
        self.bound_tool_names.append(
            sorted(getattr(item, "name", str(item)) for item in tools)
        )
        return self


class RecordingFakeChatModel(BindableFakeChatModel):
    """Fake model that records message shapes for behavior-level checks."""

    seen_message_batches: ClassVar[list[list[BaseMessage]]] = []

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Any:
        self.seen_message_batches.append(list(messages))
        return super()._generate(
            messages,
            stop=stop,
            run_manager=run_manager,
            **kwargs,
        )


class StreamMarkerMiddleware(AgentMiddleware):
    """Emit one safe custom event so the v2 custom stream mode is exercised."""

    def before_model(self, state: Any, runtime: Any) -> None:
        del state, runtime
        from langgraph.config import get_stream_writer

        get_stream_writer()({"p0": "custom"})


@dataclass(frozen=True)
class P0Result:
    parent_checkpoint: bool
    filesystem_profile: bool
    cancellation_propagation: bool
    structured_response: bool
    schema_retry: bool
    stream_without_reasoning: bool
    summarization_profile: bool


def _register_profile() -> None:
    register_harness_profile(
        MODEL_KEY,
        HarnessProfile(
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)
        ),
    )


def _filesystem() -> FilesystemMiddleware:
    return FilesystemMiddleware(tools=["read_file", "edit_file"])


def _p0_saver() -> InMemorySaver:
    return InMemorySaver(
        serde=JsonPlusSerializer(
            allowed_msgpack_modules=[
                ("Agent.deep_agent_tools.models", "FinalAnalysisDecision")
            ]
        )
    )


def _valid_decision_args() -> dict[str, Any]:
    return {
        "outcome": "evidence_only",
        "conflict_status": "none",
        "selection_rationale": "p0",
        "confidence": "low",
    }


async def _fake_parent_and_cancel() -> tuple[bool, bool, bool, bool]:
    _register_profile()
    BindableFakeChatModel.bound_tool_names = []
    child_model = BindableFakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "FinalAnalysisDecision",
                        "args": _valid_decision_args(),
                        "id": "p0-structured",
                    }
                ],
            )
        ]
    )
    child = create_deep_agent(
        model=child_model,
        tools=[],
        subagents=[],
        middleware=[_filesystem()],
        state_schema=SpikeState,
        response_format=ToolStrategy(FinalAnalysisDecision),
        checkpointer=None,
    )

    saver = _p0_saver()
    parent_builder = StateGraph(SpikeState, context_schema=RuntimeContext)
    parent_builder.add_node("deep_agent", child)
    parent_builder.add_edge(START, "deep_agent")
    parent_builder.add_edge("deep_agent", END)
    parent = parent_builder.compile(checkpointer=saver)
    config = {"configurable": {"thread_id": "p0-parent-child"}}
    await parent.ainvoke(
        {
            "messages": [HumanMessage(content="return the structured decision")],
            "marker": "state-value",
            "files": {"p0.txt": "state-file"},
        },
        config=config,
        context={"runtime_marker": "runtime-only"},
    )
    state = await parent.aget_state(config)
    recovered = parent_builder.compile(checkpointer=saver)
    recovered_state = await recovered.aget_state(config)
    checkpoint_ok = (
        state.values.get("marker") == "state-value"
        and state.values.get("files") == {"p0.txt": "state-file"}
        and isinstance(state.values.get("structured_response"), FinalAnalysisDecision)
        and isinstance(recovered_state.values.get("structured_response"), FinalAnalysisDecision)
        and isinstance(state.values["messages"][-1], ToolMessage)
        and state.values["messages"][-1].tool_call_id == "p0-structured"
        and "runtime_marker" not in state.values
        and isinstance(recovered_state.values["messages"][-1], ToolMessage)
        and recovered_state.values["messages"][-1].tool_call_id == "p0-structured"
    )
    bound_tools = set(BindableFakeChatModel.bound_tool_names[-1])
    tools_ok = (
        BindableFakeChatModel.bound_tool_names
        and ALLOWED_FILESYSTEM_TOOLS.issubset(bound_tools)
        and bound_tools - ALLOWED_FILESYSTEM_TOOLS <= {"FinalAnalysisDecision"}
    )

    @tool
    def revoke() -> str:
        """Raise the worker control-flow exception."""

        raise JobExecutionRevoked("p0 revoked")

    revoke_model = BindableFakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "revoke", "args": {}, "id": "p0-revoke"}],
            )
        ]
    )
    revoke_graph = create_deep_agent(
        model=revoke_model,
        tools=[revoke],
        subagents=[],
        middleware=[_filesystem()],
        checkpointer=_p0_saver(),
    )
    revoke_propagation_ok = False
    try:
        await revoke_graph.ainvoke(
            {"messages": [HumanMessage(content="revoke now")]},
            config={"configurable": {"thread_id": "p0-revoke"}},
        )
    except JobExecutionRevoked:
        revoke_propagation_ok = True

    async def user_cancelled_node(_: SpikeState) -> dict[str, Any]:
        raise asyncio.CancelledError()

    cancel_builder = StateGraph(SpikeState)
    cancel_builder.add_node("cancelled", user_cancelled_node)
    cancel_builder.add_edge(START, "cancelled")
    cancel_builder.add_edge("cancelled", END)
    framework_cancel_ok = False
    try:
        await cancel_builder.compile().ainvoke({"messages": []})
    except NodeCancelledError as exc:
        framework_cancel_ok = isinstance(exc.__cause__, asyncio.CancelledError)

    RecordingFakeChatModel.seen_message_batches = []

    @tool
    def after_summary(value: str) -> str:
        """Return a value after summarization for call-id correlation."""

        return value

    summary_model = RecordingFakeChatModel(
        responses=[
            AIMessage(content="p0 summary"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "after_summary",
                        "args": {"value": "after"},
                        "id": "p0-after-summary",
                    }
                ],
            ),
            AIMessage(content="done"),
        ],
        profile={"max_input_tokens": 100},
    )
    summary_agent = create_deep_agent(
        model=summary_model,
        tools=[after_summary],
        subagents=[],
        middleware=[_filesystem()],
        checkpointer=None,
    )
    initial_messages = [
        HumanMessage(content=f"old-{index} " + ("context " * 80))
        for index in range(6)
    ]
    summary_result = await summary_agent.ainvoke({"messages": initial_messages})
    summary_message_seen = any(
        any(
            isinstance(message, HumanMessage)
            and message.additional_kwargs.get("lc_source") == "summarization"
            for message in batch
        )
        for batch in RecordingFakeChatModel.seen_message_batches
    )
    result_messages = summary_result.get("messages", [])
    post_summary_tool_ok = any(
        isinstance(message, ToolMessage)
        and message.tool_call_id == "p0-after-summary"
        for message in result_messages
    ) and any(
        isinstance(message, AIMessage)
        and any(
            call.get("id") == "p0-after-summary"
            for call in message.tool_calls
        )
        for message in result_messages
    )
    summary_behavior_ok = (
        len(RecordingFakeChatModel.seen_message_batches) >= 3
        and summary_message_seen
        and post_summary_tool_ok
        and isinstance(result_messages, list)
    )
    return (
        bool(checkpoint_ok),
        bool(tools_ok),
        bool(framework_cancel_ok and revoke_propagation_ok),
        bool(summary_behavior_ok),
    )


def _build_real_model() -> ChatOpenAI:
    model_name = os.environ.get("DEEP_AGENT_MODEL") or os.environ.get("MODEL")
    base_url = os.environ.get("DEEP_AGENT_BASE_URL") or os.environ.get("BASE_URL")
    api_key = os.environ.get("DEEP_AGENT_API_KEY") or os.environ.get("API_KEY")
    if not all((model_name, base_url, api_key)):
        raise RuntimeError("DeepSeek configuration is incomplete")
    if model_name != "deepseek-v4-flash":
        raise RuntimeError("DeepSeek P0 requires deepseek-v4-flash")
    return ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=api_key,
        use_responses_api=True,
        output_version="responses/v1",
        reasoning={"effort": "none"},
        profile={"max_input_tokens": 1_000_000},
        streaming=False,
        timeout=90,
        max_retries=0,
    )


async def _real_summary_behavior(model: ChatOpenAI) -> bool:
    """Exercise real-model summarization at the configured context threshold."""

    @tool
    def after_real_summary(value: str) -> str:
        """Return a value after the real summary middleware runs."""

        return value

    summary = create_summarization_middleware(
        model,
        StateBackend(),
        trim_tokens_to_summarize=4000,
    )
    agent = create_deep_agent(
        model=model,
        tools=[after_real_summary],
        subagents=[],
        middleware=[_filesystem(), summary],
        checkpointer=None,
    )
    result = await agent.ainvoke(
        {
            "messages": [
                HumanMessage(content="Historical fact: p0 summary behavior is under test." + (" history " * 440_000)),
                AIMessage(content="Historical assistant record: retain the marker."),
                HumanMessage(
                    content=(
                        "AFTER-SUMMARY: after the conversation is condensed, call "
                        "after_real_summary exactly once with value after."
                    )
                ),
            ]
        },
        config={"recursion_limit": 8},
    )
    result_messages = result.get("messages", [])
    tool_call_ids = {
        call.get("id")
        for message in result_messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
        if call.get("name") == "after_real_summary" and call.get("id")
    }
    tool_call_ok = bool(tool_call_ids)
    tool_output_ok = any(
        isinstance(message, ToolMessage)
        and message.tool_call_id in tool_call_ids
        for message in result_messages
    )
    history_files = result.get("files")
    history_offloaded_ok = isinstance(history_files, dict) and any(
        str(path).startswith("/conversation_history/") for path in history_files
    )
    return bool(history_offloaded_ok and tool_call_ok and tool_output_ok)


async def _real_agent_checks() -> tuple[bool, bool, bool, bool]:
    model = _build_real_model()
    summary = create_summarization_middleware(
        model,
        StateBackend(),
        trim_tokens_to_summarize=4000,
    )
    helper = summary._lc_helper
    summarization_ok = (
        model.profile == {"max_input_tokens": 1_000_000}
        and helper.trigger == ("fraction", 0.85)
        and helper.keep == ("fraction", 0.10)
        and helper.trim_tokens_to_summarize == 4000
    )

    agent = create_deep_agent(
        model=model,
        tools=[],
        subagents=[],
        middleware=[_filesystem()],
        response_format=ToolStrategy(FinalAnalysisDecision),
        checkpointer=_p0_saver(),
    )
    valid = await agent.ainvoke(
        {
            "messages": [
                HumanMessage(
                    content=(
                        "Return an evidence_only FinalAnalysisDecision. Use no primary "
                        "result, no assessments, conflict_status none, "
                        "selection_rationale p0, confidence low, and an empty "
                        "confidence_basis."
                    )
                )
            ]
        },
        config={
            "configurable": {"thread_id": "p0-real-structured"},
            "recursion_limit": 8,
        },
    )
    structured_ok = isinstance(valid.get("structured_response"), FinalAnalysisDecision)

    retry = await agent.ainvoke(
        {
            "messages": [
                HumanMessage(
                    content=(
                        "Protocol test. First submit a malformed "
                        "FinalAnalysisDecision with outcome exactly invalid_value "
                        "and omit required fields. After the framework reports a "
                        "schema error, correct it and submit a valid evidence_only "
                        "decision with no primary result, no assessments, "
                        "conflict_status none, selection_rationale p0, confidence "
                        "low, and empty confidence_basis. Do not call any other "
                        "tool."
                    )
                )
            ]
        },
        config={
            "configurable": {"thread_id": "p0-real-schema-retry"},
            "recursion_limit": 8,
        },
    )
    tool_errors = sum(
        isinstance(message, ToolMessage)
        and "error" in str(message.content).lower()
        for message in retry.get("messages", [])
    )
    schema_retry_ok = (
        tool_errors >= 1
        and isinstance(retry.get("structured_response"), FinalAnalysisDecision)
    )

    stream_types: set[str] = set()
    reasoning_blocks = 0
    stream_agent = create_deep_agent(
        model=model,
        tools=[],
        subagents=[],
        middleware=[_filesystem(), StreamMarkerMiddleware()],
        response_format=ToolStrategy(FinalAnalysisDecision),
        checkpointer=None,
    )

    def collect_messages(value: Any, output: list[BaseMessage]) -> None:
        if isinstance(value, BaseMessage):
            output.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                collect_messages(item, output)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect_messages(item, output)

    async for chunk in stream_agent.astream(
        {
            "messages": [
                HumanMessage(
                    content=(
                        "Return evidence_only; no primary result; conflict_status "
                        "none; selection_rationale p0; confidence low; empty "
                        "confidence_basis."
                    )
                )
            ]
        },
        config={
            "configurable": {"thread_id": "p0-real-stream"},
            "recursion_limit": 8,
        },
        stream_mode=["updates", "messages", "custom", "tasks"],
        version="v2",
    ):
        stream_types.add(str(chunk.get("type")))
        messages: list[BaseMessage] = []
        collect_messages(chunk.get("data"), messages)
        for message in messages:
            blocks = message.content if isinstance(message.content, list) else []
            reasoning_blocks += sum(
                isinstance(block, dict) and block.get("type") == "reasoning"
                for block in blocks
            )
    stream_ok = (
        {"updates", "messages", "custom", "tasks"}.issubset(stream_types)
        and stream_types.issubset({"updates", "messages", "custom", "tasks"})
        and reasoning_blocks == 0
    )
    real_summary_ok = await _real_summary_behavior(model)
    return structured_ok, schema_retry_ok, stream_ok, bool(
        summarization_ok and real_summary_ok
    )


async def run() -> P0Result:
    parent_ok, tools_ok, cancellation_ok, summary_behavior_ok = (
        await _fake_parent_and_cancel()
    )
    structured_ok, retry_ok, stream_ok, summarization_ok = await _real_agent_checks()
    result = P0Result(
        parent_checkpoint=parent_ok,
        filesystem_profile=tools_ok,
        cancellation_propagation=cancellation_ok,
        structured_response=structured_ok,
        schema_retry=retry_ok,
        stream_without_reasoning=stream_ok,
        summarization_profile=summarization_ok and summary_behavior_ok,
    )
    if not all(result.__dict__.values()):
        raise RuntimeError("Deep Agent P0 assertion failed")
    return result


def main() -> None:
    try:
        result = asyncio.run(run())
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
    print(json.dumps({"status": "passed", **result.__dict__}))


if __name__ == "__main__":
    main()
