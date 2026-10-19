"""本地处理确认工具与父子图中断桥接的协议测试。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from langgraph.types import Command

from Agent.causal_agent.graph import _deep_agent_parent_node
from Agent.deep_agent.context import AgentRunContext, TrustedJobIdentity
from Agent.deep_agent_tools.algorithm_specs import DEFAULT_ALGORITHM_SPECS
from Agent.deep_agent_tools.interaction import (
    DATA_PROCESSING_TOOL_NAME,
    DataProcessingTool,
    build_option_id,
)
from Agent.deep_agent_tools.identity import (
    build_deep_agent_analysis_scope,
    build_deep_agent_run_id,
)
from Agent.deep_agent_tools.models import PreprocessingPlan, PreprocessingStep
from Agent.deep_agent_tools.registry import build_default_registry


def _registry() -> object:
    return build_default_registry(
        {spec.capability_id: object() for spec in DEFAULT_ALGORITHM_SPECS}
    )


def test_interaction_tool_schema_exposes_only_controlled_operations() -> None:
    schema = DataProcessingTool(registry=_registry()).schema()
    items = schema["parameters"]["properties"]["options"]["items"]
    operations = items["properties"]["steps"]["items"]["properties"]["operation"]["enum"]

    assert operations == [
        "select_columns",
        "drop_rows",
        "numeric_impute",
        "categorical_encode",
    ]
    assert "capability_id" in schema["parameters"]["required"]


def test_interaction_tool_is_materialized_with_title_and_exclusive_name() -> None:
    tool = DataProcessingTool(registry=_registry()).to_langchain_tool()
    assert tool.name == DATA_PROCESSING_TOOL_NAME
    assert "受控处理或诊断选项" in tool.description


def test_option_identity_is_derived_from_capability_and_plan() -> None:
    plan = PreprocessingPlan(
        steps=(PreprocessingStep(operation="drop_rows", columns=("age",)),)
    )
    first = build_option_id(capability_id="causal.pc", plan_digest=plan.digest)
    second = build_option_id(capability_id="causal.pc", plan_digest=plan.digest)
    other = build_option_id(capability_id="causal.cdfm", plan_digest=plan.digest)

    assert first == second
    assert first != other


IDENTITY = TrustedJobIdentity(
    job_id="00000000-0000-0000-0000-000000000901",
    session_id="00000000-0000-0000-0000-000000000902",
    user_id=7,
    attempt_count=1,
    lease_epoch=2,
    worker_id="worker-1",
    input_identity="input-sha",
)


def _interaction_payload() -> dict:
    plan = PreprocessingPlan(
        steps=(PreprocessingStep(operation="drop_rows", columns=("age",)),)
    )
    return {
        "type": "data_processing_request",
        "prompt": "当前列存在缺失值，请选择处理方式。",
        "options": [
            {
                "option_id": build_option_id(
                    capability_id="causal.pc",
                    plan_digest=plan.digest,
                ),
                "label": "删除缺失行",
                "capability_id": "causal.pc",
                "plan": plan.model_dump(mode="json"),
                "impact": ["剩余行数：未计算"],
                "risks": ["样本量会减少"],
            }
        ],
        "expires_in_seconds": 300,
    }


def test_child_interrupt_is_rebridged_and_resumed_in_place(monkeypatch) -> None:
    """子图暂停时父图以相同公开载荷暂停，并把答复传回子图原位置。"""

    payload = _interaction_payload()
    interrupt = SimpleNamespace(id="child-question", value=payload)
    child_values = {
        "messages": [],
        "algorithm_results": {},
        "action_ledger": {},
        "rag_evidence": {},
        "web_evidence": {},
        "structured_response": None,
        "deep_agent_run_id": build_deep_agent_run_id(job_id=IDENTITY.job_id),
    }
    calls: list[object] = []
    registry = _registry()
    expected_scope = build_deep_agent_analysis_scope(
        job_id=IDENTITY.job_id,
        input_identity=IDENTITY.current_input_identity(),
        spec_digests=registry.spec_digests(),
    )

    class Child:
        async def aget_state(self, config):
            if not calls:
                return SimpleNamespace(values=child_values, interrupts=(interrupt,))
            return SimpleNamespace(values=child_values, interrupts=())

        async def ainvoke(self, value, *, config, context):
            calls.append(value)
            return {**child_values, "execution_scope": expected_scope}

    resume_answer = {"option_id": payload["options"][0]["option_id"]}
    monkeypatch.setattr("langgraph.types.interrupt", lambda value: resume_answer)

    context = AgentRunContext(execution_guard=None, trusted_identity=IDENTITY)
    asyncio.run(
        _deep_agent_parent_node(
            {"job_id": IDENTITY.job_id, "messages": [], "analysis_parameters": {}, "file_summary": {}},
            runtime=SimpleNamespace(context=context, stream_writer=lambda _payload: None),
            config={"configurable": {"thread_id": IDENTITY.job_id}},
            deep_agent=Child(),
            store=None,
            registry=registry,
            memory_init_lock=asyncio.Lock(),
        )
    )

    assert len(calls) == 1
    assert isinstance(calls[0], Command)
    assert calls[0].resume == resume_answer
