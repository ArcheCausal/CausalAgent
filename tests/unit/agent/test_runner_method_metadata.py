"""runner 元数据与结果来源模型之间的缺省版本契约；不构造执行结果。"""

import pytest

from Agent.deep_agent_tools.algorithm_specs import PC_SPEC
from Agent.deep_agent_tools.models import AlgorithmResultProvenance
from Agent.deep_agent_tools.normalization import runner_method_metadata


@pytest.mark.parametrize("raw_metadata", [
    {}, {"runner_version": None}, {"runner_version": ""},
    {"runner_version": " \t"}, {"runner_version": 1},
])
def test_unknown_runner_version_remains_unknown_in_provenance(raw_metadata):
    metadata = runner_method_metadata(raw_metadata, PC_SPEC.capability_id)
    implementation = metadata["implementation"]
    provenance = AlgorithmResultProvenance(
        job_id="metadata-contract", attempt_count=1, lease_epoch=1,
        input_identity="input", spec_digest=PC_SPEC.spec_digest,
        algorithm_runner_version=implementation.runner_version,
    )

    assert metadata["method"] == PC_SPEC.method_definition()
    assert implementation.runner_version is None
    assert provenance.algorithm_runner_version is None


def test_known_runner_version_is_preserved_in_provenance():
    metadata = runner_method_metadata({"runner_version": "pc-runner-v2"}, PC_SPEC.capability_id)
    provenance = AlgorithmResultProvenance(
        job_id="metadata-contract", attempt_count=1, lease_epoch=1,
        input_identity="input", spec_digest=PC_SPEC.spec_digest,
        algorithm_runner_version=metadata["implementation"].runner_version,
    )

    assert provenance.algorithm_runner_version == "pc-runner-v2"
