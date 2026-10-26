"""AteAdapter：图 artifact 透传与本地初检。"""

from __future__ import annotations

import asyncio

from Agent.deep_agent import TrustedJobIdentity
from Agent.deep_agent.memory import build_in_memory_backend
from Agent.deep_agent_tools.adapters.ate import AteAdapter
from Agent.deep_agent_tools.adapters.base import AdapterInput
from Agent.deep_agent_tools.error_codes import SafeErrorCode
from Agent.deep_agent_tools.fake_algorithm_executor import FakeAlgorithmExecutor
from Agent.deep_agent_tools.models import DataProfile


IDENTITY = TrustedJobIdentity(
    job_id="00000000-0000-0000-0000-000000000001",
    session_id="00000000-0000-0000-0000-000000000002",
    user_id=7,
    attempt_count=0,
    lease_epoch=1,
    worker_id="worker-1",
    input_identity="input-sha",
)

GRAPH = {
    "graph_semantics": "pdag",
    "nodes": ["z", "x", "y"],
    "edges": [
        {"source": "z", "target": "x", "edge_type": "directed"},
        {"source": "x", "target": "y", "edge_type": "directed"},
        {"source": "z", "target": "y", "edge_type": "directed"},
    ],
}

PARAMETERS = {"graph_result_ref": "inv-1:0", "treatment": "x", "outcome": "y"}


def _profile(*, rows: int = 250) -> DataProfile:
    return DataProfile(
        row_count=rows,
        column_count=3,
        column_names=("z", "x", "y"),
        numeric_columns=("z", "x", "y"),
    )


def test_adapter_forwards_graph_artifact_to_command() -> None:
    executor = FakeAlgorithmExecutor()
    adapter = AteAdapter(
        executor=executor,
        raw_backend=build_in_memory_backend(user_id=7),
    )
    result = asyncio.run(
        adapter.run(
            parameters=PARAMETERS,
            adapter_input=AdapterInput(
                data_profile=_profile(),
                input_identity="input-sha",
                artifacts={"standardized_graph": GRAPH},
            ),
            trusted_context=IDENTITY,
            provider_call_id="call-1",
            response_identity="response-1",
        )
    )
    assert result.status == "valid"
    assert executor.calls[0].command.artifacts == {"standardized_graph": GRAPH}
    assert result.provenance.preprocessing_recipe_digest


def test_adapter_refuses_missing_graph_artifact() -> None:
    adapter = AteAdapter(executor=FakeAlgorithmExecutor())
    status = adapter.validate_input(
        AdapterInput(data_profile=_profile(), input_identity="input-sha")
    )
    assert status == ("not_ready", SafeErrorCode.ALGORITHM_NOT_READY)


def test_adapter_accepts_ready_data_with_graph() -> None:
    adapter = AteAdapter(executor=FakeAlgorithmExecutor())
    status = adapter.validate_input(
        AdapterInput(
            data_profile=_profile(),
            input_identity="input-sha",
            execution_columns=("x", "y"),
            artifacts={"standardized_graph": GRAPH},
        )
    )
    assert status is None
