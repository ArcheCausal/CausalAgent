"""CDFM runner 失败时的受控诊断字段契约。"""

from __future__ import annotations

import pytest

from Agent.causal import cdfm_runner


def test_load_failure_reports_controlled_diagnostics(tmp_path) -> None:
    """真实文件读取异常经诊断函数保存类型、消息和本地权重来源。"""
    weights_dir = tmp_path / "cdfm-weights"
    weights_dir.mkdir()
    with pytest.raises(FileNotFoundError) as failure:
        (weights_dir / "model.safetensors").read_bytes()
    payload = cdfm_runner._failure(
        "AlgorithmExecutionError",
        "CDFM 模型加载失败。",
        diagnostics=cdfm_runner._failure_diagnostics(failure.value, str(weights_dir)),
    )

    assert payload["success"] is False
    assert payload["error_type"] == "AlgorithmExecutionError"
    diagnostics = payload["diagnostics"]
    assert diagnostics["error_type"] == "FileNotFoundError"
    assert diagnostics["model_path"] == str(weights_dir)
    assert diagnostics["model_source"] == "local_dir"
    assert len(diagnostics["error_message"]) <= 500
    assert "\n" not in diagnostics["error_message"]


def test_load_failure_message_is_truncated() -> None:
    """超长异常消息必须截断，避免把第三方正文整段带进诊断。"""
    with pytest.raises(KeyError) as failure:
        {}["x" * 4000]
    diagnostics = cdfm_runner._failure_diagnostics(failure.value, "DMIRLAB/CDFM")
    assert diagnostics["error_type"] == "KeyError"
    assert len(diagnostics["error_message"]) == 500
    assert diagnostics["model_source"] == "hub"
