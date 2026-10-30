"""用真实 MCP 服务、进程池与 causal-learn 执行本地冻结 CSV。

只覆盖进程内服务边界；不代替 HTTP 传输、MySQL 权威读取或 worker 验收。
"""

from dataclasses import dataclass
from copy import deepcopy
from secrets import token_hex
from collections.abc import Mapping

import numpy as np
import pandas as pd

from Agent.CausalAgentMCP.auth import sign_invocation
from Agent.CausalAgentMCP.config import McpServerConfig
from Agent.CausalAgentMCP.executor_pool import BoundedProcessPool
from Agent.CausalAgentMCP.runner_registry import build_default_registry
from Agent.CausalAgentMCP.service import CausalMcpService, EffectValidatedInput
from Agent.data_processing.validation import validate_candidate
from Agent.data_processing.parameters import execution_parameters, parameter_identity
from Agent.deep_agent_tools.algorithm_specs import DEFAULT_ALGORITHM_SPECS
from Agent.measurement_semantics import (
    ValidatedAlgorithmInput, normalize_semantic_kinds,
    semantic_kinds_from_validation_report,
)
from Agent.deep_agent_tools.algorithm_executor import (
    AlgorithmExecutionResponse, validate_executor_command, validate_executor_result,
)
from Agent.deep_agent_tools.models import (
    AlgorithmExecutionCommand, AlgorithmResult, McpInvocationContext,
)
from Agent.deep_agent_tools.preprocessing import build_data_profile_from_csv


def pc_csv() -> str:
    rng = np.random.default_rng(108)
    source = rng.normal(size=100)
    return pd.DataFrame({"x": source, "y": 0.8 * source + rng.normal(size=100)}).to_csv(index=False)


PC_CSV = pc_csv()
# 两行非恒定数据通过输入检查，但真实 Fisher-Z 无法估计该样本的相关性。
PC_INSUFFICIENT_SAMPLE_CSV = "x,y\n1,2\n2,4\n"


@dataclass(frozen=True)
class ExecutionCall:
    command: AlgorithmExecutionCommand
    trusted_context: McpInvocationContext


class LocalAlgorithmExecutor:
    """记录真实调用，并通过正式服务签名校验、进程执行及结果校验。"""

    def __init__(
        self, csv_data: str = PC_CSV, *, semantic_kinds: Mapping[str, str] | None = None,
        executor_pool: BoundedProcessPool | None = None,
        analysis_parameters: Mapping[str, object] | None = None,
        effect_execution_context: Mapping[str, object] | None = None,
    ) -> None:
        self.csv_data = csv_data
        self.executor_pool = executor_pool
        self.data_profile = build_data_profile_from_csv(csv_data)
        kinds = normalize_semantic_kinds(
            {} if semantic_kinds is None else semantic_kinds,
            columns=self.data_profile.column_names,
        )
        self.analysis_parameters = (
            {"target": None, "treatment": None, "effect_request": None}
            if analysis_parameters is None else deepcopy(dict(analysis_parameters))
        )
        self.analysis_parameters.setdefault("measurement_semantics", [
                {"column": column, "kind": kind, "evidence": f"{column}: {kind}"}
                for column, kind in kinds.items()
            ])
        self.metadata = {
            "summary": "本地算法输入，不执行转换",
            "observation_unit": "CSV 中的一条观测记录",
            "transformations": ["保留原始行列和值"],
            "variable_semantics": {},
        }
        if effect_execution_context is not None:
            self.metadata["effect_execution_context"] = deepcopy(dict(effect_execution_context))
        self.validation_report = validate_candidate(
            data_bytes=csv_data.encode("utf-8"),
            script_bytes=b"import pandas as pd\n",
            metadata=self.metadata,
            analysis_parameters=self.analysis_parameters,
            specifications=DEFAULT_ALGORITHM_SPECS,
            max_bytes=max(1, len(csv_data.encode("utf-8"))),
        )
        validated_kinds = semantic_kinds_from_validation_report(self.validation_report)
        resolved = execution_parameters(self.analysis_parameters, self.metadata)
        resolved["research_identity"] = parameter_identity(self.analysis_parameters)
        self.validated_input = EffectValidatedInput(
            csv_data=csv_data,
            semantic_kinds=validated_kinds,
            effect_parameters=resolved,
        )
        self.calls: list[ExecutionCall] = []

    async def execute(self, command, trusted_context) -> AlgorithmExecutionResponse:
        validate_executor_command(command, trusted_context=trusted_context)
        self.calls.append(ExecutionCall(command.model_copy(deep=True), trusted_context.model_copy(deep=True)))
        pool = self.executor_pool if self.executor_pool is not None else BoundedProcessPool(process_workers=1)
        config = McpServerConfig(
            service_token=token_hex(16), signing_key_current=token_hex(16),
            signing_key_previous=None, signing_key_id=trusted_context.key_id,
            signing_key_previous_id="previous",
        )

        def read_input(context: McpInvocationContext) -> ValidatedAlgorithmInput:
            validate_executor_command(command, trusted_context=context)
            return self.validated_input

        service = CausalMcpService(
            config=config, registry=build_default_registry(),
            executor_pool=pool, authority_reader=read_input,
        )
        await pool.start()
        try:
            payload = await service.execute_payload({
                "command": command.model_dump(mode="json"),
                "trusted_context": trusted_context.model_dump(mode="json"),
                "signature": sign_invocation(trusted_context, command, config.signing_key_current),
            })
        finally:
            await pool.close()
        assert payload["ok"] is True, payload
        result = AlgorithmResult.model_validate(payload["result"])
        validate_executor_result(result, command=command, trusted_context=trusted_context)
        return AlgorithmExecutionResponse(result=result, raw_payload=payload.get("raw_payload"))
