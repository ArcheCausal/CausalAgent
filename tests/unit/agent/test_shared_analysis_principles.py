"""共享提示词的实际构造契约，不调用或替代模型。"""

from Agent.analysis_principles import CAUSAL_ANALYSIS_PRINCIPLES
from Agent.causal_agent.back_prompt import causal_prompt, causal_report_prompt, data_prompt
from Agent.deep_agent.prompts import build_deep_agent_system_prompt


def test_both_runtime_entries_use_the_same_principles_and_keep_local_contracts():
    assert causal_prompt() == CAUSAL_ANALYSIS_PRINCIPLES
    deep = build_deep_agent_system_prompt("本次限制")
    assert deep.startswith(CAUSAL_ANALYSIS_PRINCIPLES)
    assert "FinalAnalysisDecision" in deep and "request_data_processing" in deep
    assert deep.endswith("本次限制")
    assert "只有本次真正估计并识别了效应时才解释效应量" in causal_report_prompt()
    assert "未检出非线性不证明线性机制" in data_prompt()


def test_structure_discovery_does_not_require_treatment_or_assert_unchecked_assumptions():
    for prompt in (causal_prompt(), build_deep_agent_system_prompt()):
        assert "结构发现无需指定处理变量或结果变量" in prompt
        assert "未执行检查不等于检查通过" in prompt
        assert "整数或偏态分布不能证明忠实性很可能不成立" in prompt
