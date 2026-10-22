"""真实 CDFM 依赖在空本地权重目录下的受控加载失败；不执行模型推理。"""

from importlib.util import find_spec

import pytest

from Agent.causal.cdfm_runner import run_cdfm_analysis


@pytest.mark.skipif(find_spec("cdfm") is None, reason="requires the real cdfm dependency")
def test_empty_local_weights_return_controlled_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("CDFM_MODEL_PATH", str(tmp_path))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")

    result = run_cdfm_analysis("x,y\n1,2\n2,3\n")

    assert result["success"] is False
    assert result["error_type"] == "AlgorithmExecutionError"
    diagnostics = result["diagnostics"]
    assert diagnostics["model_source"] == "local_dir"
    assert diagnostics["model_path"] == str(tmp_path)
    assert diagnostics["error_type"]
    assert 0 < len(diagnostics["error_message"]) <= 500
    assert "\n" not in diagnostics["error_message"]
