"""在新进程中检查共享契约与真实 MCP 入口的导入边界。"""

from pathlib import Path
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "module_name",
    ["Agent.deep_agent_tools", "Agent.CausalAgentMCP.app"],
)
def test_import_does_not_load_agent_runtime(module_name: str) -> None:
    code = f"""
import importlib
import sys

importlib.import_module({module_name!r})
import Agent.deep_agent_tools as contracts

for name in contracts.__all__:
    assert hasattr(contracts, name), name

forbidden = sorted(
    name for name in sys.modules
    if name.startswith(('langgraph', 'langchain', 'Agent.deep_agent.'))
    or name == 'Agent.deep_agent'
    or name in {{
        'Agent.deep_agent_tools.dependency_planner',
        'Agent.deep_agent_tools.algorithm_tools',
        'Agent.deep_agent_tools.rag_evidence_tool',
        'Agent.deep_agent_tools.web_evidence_tool',
        'Agent.deep_agent_tools.fake_algorithm_executor',
    }}
)
assert not forbidden, forbidden
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
