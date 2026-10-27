"""确认算法工具只接收分析参数，不提供数据改写入口。"""

from Agent.deep_agent_tools.algorithm_specs import build_tool_schema_snapshot
from Agent.deep_agent_tools.models import AlgorithmExecutionCommand


def test_algorithm_execution_command_has_no_preprocessing_fields() -> None:
    properties = AlgorithmExecutionCommand.model_json_schema()["properties"]

    assert "preprocessing" not in properties
    assert "approval" not in properties
    assert "input_identity" in properties


def test_registered_algorithm_tools_do_not_expose_preprocessing_parameters() -> None:
    snapshot = build_tool_schema_snapshot()

    assert snapshot
    for item in snapshot:
        properties = item["tool"]["parameters"]["properties"]
        assert "preprocessing" not in properties
        assert "approval" not in properties
        assert "数据要求" in item["tool"]["description"]
        assert "数据整理工作在分析开始前完成" in item["tool"]["description"]
