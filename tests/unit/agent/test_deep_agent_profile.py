"""P2-U 工具裁剪、预算和 filesystem permission 测试。"""

import sys
import types

from Agent.deep_agent.profile import (
    DEFAULT_FILESYSTEM_PERMISSIONS,
    DeepAgentBudget,
    DeepAgentProfile,
    expected_model_tool_names,
    is_filesystem_write_allowed,
)
from Agent.deep_agent.context import AgentRunContext
from Agent.deep_agent.graph import (
    DeepAgentGraphConfig,
    VerifiedProviderResponseId,
    _build_message_identity_middleware,
    build_deep_agent,
)
from Agent.deep_agent.state import ProjectDeepAgentState


def _identity_middleware(monkeypatch, *, provider_response_id_extractor=None):
    """构造不依赖真实 LangChain 的 identity middleware。"""

    langchain = types.ModuleType("langchain")
    langchain_agents = types.ModuleType("langchain.agents")
    langchain_middleware = types.ModuleType("langchain.agents.middleware")
    langchain_middleware.AgentMiddleware = object
    monkeypatch.setitem(sys.modules, "langchain", langchain)
    monkeypatch.setitem(sys.modules, "langchain.agents", langchain_agents)
    monkeypatch.setitem(
        sys.modules,
        "langchain.agents.middleware",
        langchain_middleware,
    )
    return _build_message_identity_middleware(
        provider_response_id_extractor=provider_response_id_extractor
    )


def test_only_memory_files_are_model_writable() -> None:
    assert is_filesystem_write_allowed("/memories/preferences.md")
    assert is_filesystem_write_allowed("/memories/research_background.md")
    assert not is_filesystem_write_allowed("/raw_algorithm_results/a.json")
    assert not is_filesystem_write_allowed("/large_tool_results/a.json")
    assert DEFAULT_FILESYSTEM_PERMISSIONS[0].mode == "allow"
    assert DEFAULT_FILESYSTEM_PERMISSIONS[1].mode == "deny"


def test_profile_keeps_frozen_budget_and_tool_allowlist() -> None:
    profile = DeepAgentProfile(model_name="deepseek-v4-flash", context_window_tokens=1000)
    assert profile.filesystem_tools == ("read_file", "edit_file")
    assert profile.budget == DeepAgentBudget()
    assert profile.model_profile() == {"max_input_tokens": 1000}
    assert expected_model_tool_names(["causal_pc", "rag_evidence_search"]) == (
        "causal_pc",
        "edit_file",
        "rag_evidence_search",
        "read_file",
    )


def test_profile_reads_budget_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("DEEP_AGENT_CONTEXT_WINDOW_TOKENS", "2048")
    monkeypatch.setenv("DEEP_AGENT_MODEL_CALL_LIMIT", "7")
    monkeypatch.setenv("DEEP_AGENT_TOOL_CALL_LIMIT", "5")
    profile = DeepAgentProfile.from_environment()
    assert profile.context_window_tokens == 2048
    assert profile.budget.model_call_limit == 7
    assert profile.budget.tool_call_limit == 5


def test_identity_preserves_current_verified_provider_response_id(monkeypatch) -> None:
    def current_response_provider_id(state, runtime):
        return runtime.current_response_provider_id

    middleware = _identity_middleware(
        monkeypatch,
        provider_response_id_extractor=current_response_provider_id,
    )
    monkeypatch.setattr(
        "Agent.deep_agent.graph.new_message_execution_id",
        lambda: "fallback-for-provider-response",
    )

    update = middleware.after_model(
        {
            "provider_response_id": "stale-provider-response",
            "messages": [
                {
                    "id": "unverified-message-id",
                    "tool_calls": [{"id": "unverified-call-id"}],
                }
            ],
        },
        types.SimpleNamespace(
            current_response_provider_id=VerifiedProviderResponseId(
                "provider-response-1"
            )
        ),
    )

    assert update == {
        "message_execution_id": "fallback-for-provider-response",
        "provider_response_id": "provider-response-1",
    }


def test_identity_uses_new_local_fallback_without_provider_response_id(monkeypatch) -> None:
    middleware = _identity_middleware(monkeypatch)
    monkeypatch.setattr(
        "Agent.deep_agent.graph.new_message_execution_id",
        lambda: "fresh-local-fallback",
    )

    update = middleware.after_model(
        {"provider_response_id": "stale-provider-response"}, None
    )

    assert update["message_execution_id"] == "fresh-local-fallback"
    assert update["provider_response_id"] is None


def test_identity_does_not_reuse_previous_round_fallback(monkeypatch) -> None:
    middleware = _identity_middleware(monkeypatch)
    fallback_ids = iter(("local-fallback-1", "local-fallback-2"))
    monkeypatch.setattr(
        "Agent.deep_agent.graph.new_message_execution_id",
        lambda: next(fallback_ids),
    )

    first_update = middleware.after_model({}, None)
    second_update = middleware.after_model(
        {
            "message_execution_id": first_update["message_execution_id"],
            "provider_response_id": "stale-provider-response",
        },
        None,
    )

    assert first_update["provider_response_id"] is None
    assert second_update["provider_response_id"] is None
    assert first_update["message_execution_id"] == "local-fallback-1"
    assert second_update["message_execution_id"] == "local-fallback-2"
    assert first_update["message_execution_id"] != second_update["message_execution_id"]


def test_identity_does_not_infer_provider_id_from_unverified_message_fields(monkeypatch) -> None:
    middleware = _identity_middleware(monkeypatch)
    monkeypatch.setattr(
        "Agent.deep_agent.graph.new_message_execution_id",
        lambda: "message-field-fallback",
    )

    update = middleware.after_model(
        {
            "messages": [
                {
                    "id": "message-id",
                    "tool_calls": [{"id": "call-id"}],
                }
            ]
        },
        None,
    )

    assert update["provider_response_id"] is None
    assert update["message_execution_id"] == "message-field-fallback"


def test_real_graph_builder_wires_state_context_profile_and_harness(monkeypatch) -> None:
    captured = {}

    class FakeModel:
        model_name = "deepseek-v4-flash"

        def model_copy(self, *, update):
            copied = FakeModel()
            copied.profile = update["profile"]
            return copied

    class Middleware:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class LimitMiddleware(Middleware):
        def __init__(self, run_limit, exit_behavior="end"):
            super().__init__(run_limit=run_limit, exit_behavior=exit_behavior)

    class Permission(Middleware):
        pass

    class GeneralPurposeSubagentProfile(Middleware):
        pass

    class HarnessProfile(Middleware):
        pass

    deepagents = types.ModuleType("deepagents")
    deepagents.create_deep_agent = lambda **kwargs: captured.setdefault("kwargs", kwargs)
    deepagents.GeneralPurposeSubagentProfile = GeneralPurposeSubagentProfile
    deepagents.HarnessProfile = HarnessProfile
    deepagents.register_harness_profile = lambda key, profile: captured.setdefault(
        "harness", (key, profile)
    )
    deepagents_middleware = types.ModuleType("deepagents.middleware")
    deepagents_middleware.FilesystemMiddleware = Middleware
    deepagents_middleware.FilesystemPermission = Permission
    langchain_agents = types.ModuleType("langchain.agents")
    langchain_structured_output = types.ModuleType("langchain.agents.structured_output")
    langchain_structured_output.ToolStrategy = lambda schema: ("tool-strategy", schema)
    langchain_middleware = types.ModuleType("langchain.agents.middleware")
    langchain_middleware.ModelCallLimitMiddleware = LimitMiddleware
    langchain_middleware.ToolCallLimitMiddleware = LimitMiddleware
    langchain_middleware.SummarizationMiddleware = Middleware
    langchain_middleware.AgentMiddleware = object
    monkeypatch.setitem(sys.modules, "deepagents", deepagents)
    monkeypatch.setitem(sys.modules, "deepagents.middleware", deepagents_middleware)
    monkeypatch.setitem(sys.modules, "langchain.agents", langchain_agents)
    monkeypatch.setitem(
        sys.modules,
        "langchain.agents.structured_output",
        langchain_structured_output,
    )
    monkeypatch.setitem(sys.modules, "langchain.agents.middleware", langchain_middleware)

    class DomainTool:
        def to_langchain_tool(self):
            return "materialized-tool"

    graph = build_deep_agent(
        model=FakeModel(),
        domain_tools=[DomainTool()],
        backend=object(),
        store=object(),
        config=DeepAgentGraphConfig(context_window_tokens=4096),
    )

    assert graph is captured["kwargs"]
    assert captured["harness"][0] == "openai"
    assert captured["harness"][1].kwargs["general_purpose_subagent"].kwargs == {
        "enabled": False
    }
    assert graph["state_schema"] is ProjectDeepAgentState
    assert graph["context_schema"] is AgentRunContext
    assert graph["tools"] == ["materialized-tool"]
    assert graph["model"].profile["max_input_tokens"] == 4096
    assert graph["subagents"] == []
    assert graph["middleware"][0].kwargs["tools"] == ["read_file", "edit_file"]
    assert graph["middleware"][0].kwargs["_permissions"] == graph["permissions"]
    assert graph["middleware"][1].kwargs["trigger"] == ("fraction", 0.85)
    assert graph["middleware"][1].kwargs["keep"] == ("fraction", 0.10)
    identity_update = graph["middleware"][-1].after_model({}, None)
    assert identity_update["message_execution_id"]
    assert identity_update["provider_response_id"] is None
