"""使用真实输入类型验证 CDFM 的可信数据来源边界。"""

from __future__ import annotations

import pytest

from Agent.deep_agent_tools.adapters.base import AdapterInput
from Agent.deep_agent_tools.adapters.cdfm import CDFMAdapter
from Agent.deep_agent_tools.error_codes import SafeErrorCode
from Agent.deep_agent_tools.models import DataProfile


@pytest.mark.parametrize(
    "dataset_authority_available",
    [False, True],
)
@pytest.mark.parametrize("missing_values_present", [False, True])
def test_cdfm_requires_dataset_authority(
    dataset_authority_available: bool,
    missing_values_present: bool,
) -> None:
    profile = DataProfile(
        row_count=2,
        column_count=2,
        column_names=("x", "y"),
        numeric_columns=("x", "y"),
        missing_columns=("x",) if missing_values_present else (),
    )
    adapter_input = AdapterInput(
        data_profile=profile,
        input_identity="input-sha",
        missing_values_present=missing_values_present,
        dataset_authority_available=dataset_authority_available,
    )
    # 只调用输入校验；此路径不需要执行器，也不执行算法或访问外部服务。
    adapter = CDFMAdapter(executor=None)

    result = adapter.validate_input(adapter_input)

    if dataset_authority_available:
        assert result is None
    else:
        assert result == ("invalid_input", SafeErrorCode.ALGORITHM_INPUT_INVALID)


def test_cdfm_rejects_input_without_explicit_dataset_authority() -> None:
    adapter_input = AdapterInput(
        data_profile=DataProfile(
            row_count=2,
            column_count=2,
            column_names=("x", "y"),
            numeric_columns=("x", "y"),
        ),
        input_identity="input-sha",
    )
    adapter = CDFMAdapter(executor=None)

    assert adapter.validate_input(adapter_input) == (
        "invalid_input",
        SafeErrorCode.ALGORITHM_INPUT_INVALID,
    )
