"""冻结的 filesystem allow/deny first-match 规则。"""

from Agent.deep_agent.profile import is_filesystem_write_allowed


def test_permission_rules_allow_exact_memory_files_and_deny_everything_else() -> None:
    assert is_filesystem_write_allowed("/memories/preferences.md") is True
    assert is_filesystem_write_allowed("/memories/research_background.md") is True
    assert is_filesystem_write_allowed("/memories/other.md") is False
    assert is_filesystem_write_allowed("/raw_algorithm_results/invocation.json") is False

