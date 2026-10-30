"""系统提示保留诊断阈值与科学选择边界，不强制选择估计器。"""

from Agent.deep_agent_tools.functional_form import RATIO_LINE, SIGNAL_FLOOR
from Agent.deep_agent.prompts import DEEP_AGENT_SYSTEM_PROMPT, MANDATORY_ALGORITHM_INSTRUCTION


def test_mandatory_instruction_formats_shared_thresholds() -> None:
    assert f"{SIGNAL_FLOOR}" in MANDATORY_ALGORITHM_INSTRUCTION
    assert f"{RATIO_LINE}" in MANDATORY_ALGORITHM_INSTRUCTION
    assert "causal_g_formula" in MANDATORY_ALGORITHM_INSTRUCTION


def test_prompt_requires_confirmed_definition_without_implicit_contrast() -> None:
    assert "analysis_parameters.effect_request" in MANDATORY_ALGORITHM_INSTRUCTION
    assert "不能推断意图或补猜(0,1)" in MANDATORY_ALGORITHM_INSTRUCTION
    assert "程序不按方法名强制采用结果" in MANDATORY_ALGORITHM_INSTRUCTION
    assert "不能以差异直接证明非线性" in MANDATORY_ALGORITHM_INSTRUCTION
    assert "不证明线性、方法等价" in MANDATORY_ALGORITHM_INSTRUCTION


def test_prompt_separates_primary_graph_and_primary_effect() -> None:
    assert "primary_graph_result_ref" in DEEP_AGENT_SYSTEM_PROMPT
    assert "primary_effect_result_ref" in DEEP_AGENT_SYSTEM_PROMPT
    assert "primary_result_ref 仅作兼容投影" in DEEP_AGENT_SYSTEM_PROMPT
    assert "可以保留主图并说明为何未采用有效估计" in DEEP_AGENT_SYSTEM_PROMPT
