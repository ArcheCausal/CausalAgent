"""CDFM runner 失败时的受控诊断字段契约。"""

from __future__ import annotations

import sys
import types

import pytest

from Agent.causal import cdfm_runner


class _Boom:
    """在模型加载阶段抛异常的 CDFM 替身，用于验证诊断字段。"""

    @classmethod
    def from_pretrained(cls, *_args, **_kwargs):
        raise RuntimeError("权重文件缺失 /opt/cdfm-weights/model.safetensors")


def _install_fake_cdfm(monkeypatch: pytest.MonkeyPatch, model: type) -> None:
    """注入最小 cdfm 模块替身；runner 只按契约取用 ``CDFM`` 类。"""
    module = types.ModuleType("cdfm")
    module.CDFM = model
    monkeypatch.setitem(sys.modules, "cdfm", module)


def test_load_failure_reports_controlled_diagnostics(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """加载失败必须带回错误类型、截断消息和权重来源，便于区分本地卷与 Hub。"""
    _install_fake_cdfm(monkeypatch, _Boom)
    weights_dir = tmp_path / "cdfm-weights"
    weights_dir.mkdir()
    monkeypatch.setenv("CDFM_MODEL_PATH", str(weights_dir))

    payload = cdfm_runner.run_cdfm_analysis("A,B\n1,2\n3,4\n")

    assert payload["success"] is False
    assert payload["error_type"] == "AlgorithmExecutionError"
    diagnostics = payload["diagnostics"]
    assert diagnostics["error_type"] == "RuntimeError"
    assert diagnostics["model_path"] == str(weights_dir)
    assert diagnostics["model_source"] == "local_dir"
    assert len(diagnostics["error_message"]) <= 500
    assert "\n" not in diagnostics["error_message"]


def test_load_failure_message_is_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    """超长异常消息必须截断，避免把第三方正文整段带进诊断。"""
    class VerboseBoom:
        @classmethod
        def from_pretrained(cls, *_args, **_kwargs):
            raise RuntimeError("x" * 4000)

    _install_fake_cdfm(monkeypatch, VerboseBoom)
    monkeypatch.setenv("CDFM_MODEL_PATH", "DMIRLAB/CDFM")

    payload = cdfm_runner.run_cdfm_analysis("A,B\n1,2\n3,4\n")

    diagnostics = payload["diagnostics"]
    assert len(diagnostics["error_message"]) == 500
    assert diagnostics["model_source"] == "hub"
