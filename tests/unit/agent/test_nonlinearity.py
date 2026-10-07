"""非线性度信号：把模块自检接进 pytest，断言本体在模块里只写一份。"""

from __future__ import annotations

import pandas as pd

from Agent.Processing.fold_processing import get_data_summary
from Agent.Processing.nonlinearity import PAYLOAD_KEYS, _self_check, measure_nonlinearity


def test_module_self_check_passes() -> None:
    _self_check()


def test_measure_never_raises_and_always_returns_the_agreed_payload() -> None:
    frame = pd.DataFrame(
        {
            "x0": [float("nan")] * 200,
            "x1": ["text"] * 200,
            "x2": [1.0] * 200,
        }
    )
    payload = measure_nonlinearity(frame, get_data_summary(frame))
    assert set(payload) == PAYLOAD_KEYS
    assert payload["verdict"] == "insufficient"
    assert payload["reason"]

    assert set(measure_nonlinearity(pd.DataFrame(), {})) == PAYLOAD_KEYS
    assert measure_nonlinearity(pd.DataFrame(), None)["verdict"] == "insufficient"
