"""真实纯函数的确定性契约检查；这些输入不代表算法实跑证据。"""

import json

import pytest

from Agent.causal_agent.analysis_context import normalize_algorithm_summary
from Agent.deep_agent_tools.algorithm_specs import DEFAULT_ALGORITHM_SPECS, CDFM_SPEC, DIRECT_LINGAM_SPEC
from Agent.deep_agent_tools.models import AlgorithmResult, AlgorithmResultProvenance, StandardizedGraph
from Agent.deep_agent_tools.normalization import RunnerContractError, runner_method_metadata


@pytest.mark.parametrize("spec", DEFAULT_ALGORITHM_SPECS)
def test_method_snapshot_survives_result_state_and_context_projection(spec):
    metadata = runner_method_metadata(
        {"runner_version": "contract-input-v1",
         "implementation": {"package": "contract-input", "version": "1",
                            "weights": {"revision": "revision-input"}, "secret": "excluded"}},
        spec.capability_id,
    )
    result = AlgorithmResult(
        result_ref="invocation:0", invocation_id="invocation", provider_call_id="call",
        capability_id=spec.capability_id, capability_version=spec.version, status="valid",
        standardized_graph=StandardizedGraph(graph_semantics=spec.graph_semantics, nodes=["A", "B"]),
        provenance=AlgorithmResultProvenance(job_id="job", attempt_count=1, lease_epoch=1,
                                             input_identity="input", spec_digest=spec.spec_digest),
        **metadata,
    )
    restored = AlgorithmResult.model_validate_json(result.model_dump_json())
    summary = json.loads(json.dumps(normalize_algorithm_summary(restored)))
    assert summary["method"] == spec.method_definition().model_dump(mode="json")
    assert summary["implementation"]["weights"]["revision"] == "revision-input"
    assert "secret" not in json.dumps(summary)
    tool = spec.model_description()
    assert all(assumption in tool for assumption in summary["method"]["assumptions"])
    assert all(meaning in tool for meaning in summary["method"]["edge_meanings"].values())


def test_runner_method_identity_cannot_be_replaced_by_another_methods_assumptions():
    with pytest.raises(RunnerContractError):
        runner_method_metadata({"method": DIRECT_LINGAM_SPEC.method_definition().model_dump(mode="json")}, CDFM_SPEC.capability_id)


def test_cdfm_numeric_storage_requirement_does_not_assert_continuous_measurement():
    method = CDFM_SPEC.method_definition()
    assert "连续数值" not in CDFM_SPEC.model_description()
    assert "数值准入不证明测量语义连续" in CDFM_SPEC.model_description()
    assert "continuous_tabular_dataset" not in method.input_requirements
