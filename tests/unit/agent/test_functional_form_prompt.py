"""系统提示必须与门禁阈值同源。"""

from Agent.deep_agent_tools.functional_form import RATIO_LINE, SIGNAL_FLOOR
from Agent.deep_agent.prompts import MANDATORY_ALGORITHM_INSTRUCTION


def test_mandatory_instruction_formats_shared_thresholds() -> None:
    assert f"{SIGNAL_FLOOR}" in MANDATORY_ALGORITHM_INSTRUCTION
    assert f"{RATIO_LINE}" in MANDATORY_ALGORITHM_INSTRUCTION
    assert "causal_g_formula" in MANDATORY_ALGORITHM_INSTRUCTION
