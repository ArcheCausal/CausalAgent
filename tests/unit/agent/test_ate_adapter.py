"""AteAdapter：图 artifact 透传与本地初检。"""

from __future__ import annotations

import asyncio

from Agent.deep_agent import TrustedJobIdentity
from Agent.deep_agent.memory import build_in_memory_backend
from Agent.deep_agent_tools.adapters.ate import AteAdapter
from Agent.deep_agent_tools.adapters.base import AdapterInput
from Agent.deep_agent_tools.error_codes import SafeErrorCode
from tests.support.local_algorithm_execution import LocalAlgorithmExecutor
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
    executor = LocalAlgorithmExecutor("z,x,y\n0,1,2\n1,3,7\n2,2,5\n3,5,11\n4,4,9\n")
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


def test_adapter_refuses_missing_graph_artifact() -> None:
    adapter = AteAdapter(executor=None)
    status = adapter.validate_input(
        AdapterInput(data_profile=_profile(), input_identity="input-sha")
    )
    assert status == ("not_ready", SafeErrorCode.ALGORITHM_NOT_READY)


def test_adapter_accepts_ready_data_with_graph() -> None:
    adapter = AteAdapter(executor=None)
    status = adapter.validate_input(
        AdapterInput(
            data_profile=_profile(),
            input_identity="input-sha",
            execution_columns=("x", "y"),
            artifacts={"standardized_graph": GRAPH},
        )
    )
    assert status is None


def test_missing_executor_is_execution_failure_after_completed_check() -> None:
    result = asyncio.run(AteAdapter(executor=None).run(
        parameters=PARAMETERS,
        adapter_input=AdapterInput(data_profile=_profile(), input_identity="input-sha",
            artifacts={"standardized_graph": GRAPH}),
        trusted_context=IDENTITY, provider_call_id="unavailable-executor", response_identity="response-1",
    ))
    assert result.status == "execution_failed"
    assert result.diagnostics.safe_error_code == SafeErrorCode.ALGORITHM_EXECUTION_FAILED
    assert result.ate_check.applicability == "ready"
    assert result.ate_check.execution_columns == ("x", "y", "z")
