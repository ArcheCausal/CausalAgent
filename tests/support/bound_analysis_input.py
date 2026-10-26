"""根据真实 CSV 和独立校验报告构造测试用分析数据绑定，不访问数据库。"""

from collections.abc import Mapping
from dataclasses import replace
from hashlib import sha256

from Agent.data_processing.validation import validate_candidate
from Agent.deep_agent.context import AnalysisInputRef, TrustedJobIdentity
from Agent.deep_agent_tools.algorithm_specs import DEFAULT_ALGORITHM_SPECS
from Agent.measurement_semantics import (
    normalize_semantic_kinds,
    semantic_kinds_from_validation_report,
)
from tests.support.local_algorithm_execution import PC_CSV


def bind_analysis_input(
    identity: TrustedJobIdentity,
    csv_data: str = PC_CSV,
    *,
    semantic_kinds: Mapping[str, str] | None = None,
    dataset_id: int = 1,
) -> TrustedJobIdentity:
    """保留来源身份，只把通过真实校验的数据集绑定到身份副本。"""
    data_bytes = csv_data.encode("utf-8")
    kinds = normalize_semantic_kinds({} if semantic_kinds is None else semantic_kinds)
    report = validate_candidate(
        data_bytes=data_bytes,
        script_bytes=b"import pandas as pd\n",
        metadata={
            "summary": "保留本地测试 CSV 的观测",
            "observation_unit": "CSV 中的一条观测记录",
            "transformations": ["保留原始行列和值"],
            "variable_semantics": {},
        },
        analysis_parameters={
            "measurement_semantics": [
                {"column": column, "kind": kind, "evidence": f"{column}: {kind}"}
                for column, kind in kinds.items()
            ],
        },
        specifications=DEFAULT_ALGORITHM_SPECS,
        max_bytes=max(1, len(data_bytes)),
    )
    ref = AnalysisInputRef()
    ref.bind(
        dataset_id=dataset_id,
        object_id=dataset_id,
        content_hash=sha256(data_bytes).hexdigest(),
        filename="analysis.csv",
        semantic_kinds=semantic_kinds_from_validation_report(report),
    )
    return replace(identity, analysis_input=ref)
