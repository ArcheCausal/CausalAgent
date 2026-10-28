"""角色映射贯通真实校验、参数投影、本地算法和最终校验；不调用模型或数据库。"""

import asyncio
from copy import deepcopy
from io import StringIO
import json

import numpy as np
import pandas as pd
import pytest
from langgraph.prebuilt import ToolRuntime
from langgraph.runtime import Runtime

from Agent.Processing.fold_processing import get_data_summary
from Agent.Processing.fold_verify import validate_analysis
from Agent.causal_agent.graph import _finalization_gate_node
from Agent.causal_agent.nodes import foldQuery
from Agent.data_processing.agent import ProcessingPlanInput
from Agent.data_processing.graph import _validation_retry_message
from Agent.data_processing.parameters import (
    PARAMETER_BINDING_KEY, execution_parameters, parameter_binding, parameter_identity,
)
from Agent.data_processing.validation import validate_candidate
from Agent.deep_agent.context import AgentRunContext, TrustedJobIdentity
from Agent.deep_agent.finalization import FinalizationGate
from Agent.deep_agent.memory import build_in_memory_backend
from Agent.deep_agent.state import to_deep_agent_input
from Agent.deep_agent_tools.adapters import build_default_adapters
from Agent.deep_agent_tools.algorithm_specs import DEFAULT_ALGORITHM_SPECS
from Agent.deep_agent_tools.algorithm_tools import build_algorithm_tools
from Agent.deep_agent_tools.registry import build_default_registry
from tests.support.bound_analysis_input import bind_analysis_input
from tests.support.local_algorithm_execution import LocalAlgorithmExecutor


JOB = "00000000-0000-0000-0000-000000000984"
ROLES = {"target": "表面粗糙度", "treatment": "冷却液流量"}
COLUMNS = {"target": "surface_roughness_ra_um", "treatment": "coolant_flow_l_min"}
PLAN = {
    "summary": "保留独立测量值", "observation_unit": "一次独立测量",
    "transformations": ["保留原始行列"], "role_columns": COLUMNS,
    "variable_semantics": {COLUMNS["target"]: "表面粗糙度，因果结果变量",
                           COLUMNS["treatment"]: "冷却液流量，因果处理变量"},
}


def source_csv():
    rng = np.random.default_rng(20261027)
    x = rng.exponential(size=300)
    return pd.DataFrame({COLUMNS["treatment"]: x,
                         COLUMNS["target"]: 2 * x + rng.uniform(-0.3, 0.3, size=300)}).to_csv(index=False)


def validate(parameters, metadata):
    return validate_candidate(data_bytes=source_csv().encode(), script_bytes=b"import pandas as pd\n",
        metadata=metadata, analysis_parameters=parameters, specifications=DEFAULT_ALGORITHM_SPECS,
        max_bytes=100_000)


def test_semantic_roles_are_preserved_while_validation_and_preprocess_use_real_columns():
    parameters = foldQuery.model_validate({
        **ROLES, "observation_unit": "每行代表一件工件的一次独立加工",
    }).model_dump()
    original = deepcopy(parameters)
    metadata = ProcessingPlanInput.model_validate(PLAN).model_dump(mode="json")
    metadata[PARAMETER_BINDING_KEY] = parameter_binding(parameters, [], job_id=JOB)
    report = validate(parameters, metadata)
    assert report["accepted"], report["errors"]
    assert {role: report[role] for role in COLUMNS} == COLUMNS
    assert report["research_roles"] == ROLES
    assert report["role_columns"] == COLUMNS
    assert parameters["observation_unit"] == "每行代表一件工件的一次独立加工"
    assert parameters["measurement_semantics"] == []
    summary = get_data_summary(pd.read_csv(StringIO(source_csv())))
    resolved = execution_parameters(parameters, metadata)
    assert validate_analysis(summary, target=resolved["target"], treatment=resolved["treatment"])[0] == 0
    projected = to_deep_agent_input({"analysis_parameters": {**summary, **parameters},
        "prepared_dataset_metadata": metadata, "data_profile": report["data_profile"]})
    assert {role: projected["analysis_parameters"][role] for role in COLUMNS} == COLUMNS
    assert json.loads(projected["messages"][0]["content"])["analysis_parameters"]["target"] == COLUMNS["target"]
    assert parameters == original
    assert parameter_identity(parameters) == metadata[PARAMETER_BINDING_KEY]["identity"]


@pytest.mark.parametrize("parameters,mapping,accepted,error", [
    (ROLES, {**COLUMNS, "target": "missing"}, False, "target_missing"),
    (ROLES, {"target": COLUMNS["target"], "treatment": COLUMNS["target"]}, False, "target_treatment_conflict"),
    ({"target": None, "treatment": None}, {}, True, None),
    ({"target": None, "treatment": None}, COLUMNS, False, "role_columns_invalid"),
    (COLUMNS, {}, True, None),
    ({"target": ROLES["target"]}, {"target": COLUMNS["target"]}, True, None),
    (ROLES, {}, False, "target_missing"),
    (ROLES, {"target": []}, False, "role_columns_invalid"),
    (ROLES, {"target": " "}, False, "role_columns_invalid"),
])
def test_role_mapping_checks_actual_columns_without_creating_or_clearing_roles(parameters, mapping, accepted, error):
    report = validate(parameters, {**PLAN, "role_columns": mapping})
    assert report["accepted"] is accepted
    if error:
        assert error in {issue["code"] for issue in report["errors"]}
    details = json.loads(_validation_retry_message(42, report).content.split("\n", 1)[1])
    assert details["research_roles"] == {role: parameters.get(role) for role in COLUMNS}
    assert details["columns"] == list(pd.read_csv(StringIO(source_csv())).columns)


def test_resolved_roles_reach_real_algorithm_tools_and_parent_finalization_gate():
    async def scenario():
        csv_data = source_csv()
        executor = LocalAlgorithmExecutor(csv_data)
        identity = bind_analysis_input(TrustedJobIdentity(job_id=JOB,
            session_id="00000000-0000-0000-0000-000000000985", user_id=7,
            attempt_count=1, lease_epoch=1, worker_id="role-mapping-test", input_identity="source-input"), csv_data)
        context = AgentRunContext(execution_guard=None, trusted_identity=identity,
            algorithm_executor=executor, filesystem_backend=build_in_memory_backend(user_id=7))
        registry = build_default_registry(build_default_adapters(executor=executor, raw_backend=context.filesystem_backend))
        parent = {"analysis_parameters": {**ROLES, "columns": list(pd.read_csv(StringIO(csv_data)).columns)},
            "prepared_dataset_metadata": deepcopy(PLAN), "data_profile": executor.data_profile, "route_decision": "fold"}
        original = deepcopy(parent["analysis_parameters"])
        child = to_deep_agent_input(parent)
        child["message_execution_id"] = "role-mapping-response"
        tools = {tool.name: tool.to_langchain_tool() for tool in build_algorithm_tools(registry, runtime_context=context)}

        async def call(name, call_id, **kwargs):
            runtime = ToolRuntime(tool_call_id=call_id, state=child, context=context,
                                  config={}, stream_writer=[].append, store=None)
            command = await tools[name].coroutine(runtime=runtime, **kwargs)
            for key in ("algorithm_results", "action_ledger"):
                child[key] = {**child[key], **command.update[key]}
            return next(iter(command.update["algorithm_results"].values()))

        discovery = await call("causal_direct_lingam", "discovery-call")
        assert discovery.status == "valid"
        effect = await call("causal_ate", "ate-call", graph_result_ref=discovery.result_ref,
            treatment=child["analysis_parameters"]["treatment"], outcome=child["analysis_parameters"]["target"])
        assert effect.status in {"valid", "not_applicable"}
        assert effect.ate_check.treatment == COLUMNS["treatment"]
        assert effect.ate_check.outcome == COLUMNS["target"]
        discovery_decision = {
            "outcome": "algorithm_supported", "primary_result_ref": discovery.result_ref,
            "result_assessments": [{"result_ref": discovery.result_ref, "disposition": "primary", "rationale": "依据真实工具结果"}],
            "conflict_status": "none", "conflicts": [], "revision_proposals": [],
            "selection_rationale": "保留实际算法结果及方法限制", "confidence": "low", "confidence_basis": ["实际工具结果"],
        }
        missing_ate = {**parent, "deep_agent_algorithm_results": {discovery.result_ref: discovery},
            "deep_agent_action_ledger": {key: record for key, record in child["action_ledger"].items()
                if record.result_ref == discovery.result_ref}, "deep_agent_structured_response": discovery_decision}
        retry = await _finalization_gate_node(missing_ate, runtime=Runtime(context=context), config={},
                                              gate=FinalizationGate(registry=registry))
        assert retry["finalization_retry_count"] == 1
        assert "causal_ate" in retry["deep_agent_retry_instruction"]
        parent.update(deep_agent_algorithm_results=child["algorithm_results"], deep_agent_action_ledger=child["action_ledger"],
            deep_agent_structured_response={
                "outcome": "algorithm_supported", "primary_result_ref": discovery.result_ref,
                "result_assessments": [{"result_ref": result.result_ref,
                    "disposition": "primary" if result is discovery else "supporting", "rationale": "依据真实工具结果"}
                    for result in (discovery, effect) if result.status == "valid"],
                "conflict_status": "none", "conflicts": [], "revision_proposals": [],
                "selection_rationale": "保留实际算法结果及方法限制", "confidence": "low", "confidence_basis": ["实际工具结果"],
            })
        result = await _finalization_gate_node(parent, runtime=Runtime(context=context), config={},
                                               gate=FinalizationGate(registry=registry))
        assert not result.get("deep_agent_retry_instruction")
        assert result.get("finalization_status") == "valid"
        assert parent["analysis_parameters"] == original
    asyncio.run(scenario())
