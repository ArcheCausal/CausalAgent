from Agent.Report.document import ReportDraft
from Agent.causal_agent.analysis_context import report_data_metadata
from Agent.causal_agent.nodes import _with_dataset_preparation_block


def _preparation_metadata() -> dict[str, object]:
    return {
        "source_filename": "Alarm-source",
        "analysis_filename": "Daily-device-data",
        "row_count": 50,
        "column_count": 4,
        "observation_unit": "每台设备一天",
        "processing_summary": "按设备与日期聚合告警记录",
        "transformations": ["按设备和日期计算告警数量"],
        "limitations": ["原始文件没有设备运行时长"],
    }


def test_report_metadata_keeps_only_safe_preparation_facts() -> None:
    parameters = {
        "data_preparation": {
            **_preparation_metadata(),
            "analysis_dataset_hash": "private-hash",
            "file_content": "private-file-content",
        }
    }

    result = report_data_metadata(parameters)

    assert result["data_preparation"] == _preparation_metadata()
    assert "analysis_dataset_hash" not in result["data_preparation"]
    assert "file_content" not in result["data_preparation"]


def test_report_has_a_stable_section_for_raw_and_analysis_data() -> None:
    draft = ReportDraft(title="分析报告", blocks=[])

    result = _with_dataset_preparation_block(
        draft,
        {"data_metadata": {"data_preparation": _preparation_metadata()}},
    )

    assert result is not draft
    assert len(result.blocks) == 1
    content = result.blocks[0].content
    assert "原始输入文件：Alarm-source" in content
    assert "实际分析数据：Daily-device-data（50 行、4 列）" in content
    assert "记录的观察单位：每台设备一天" in content
    assert "声明的转换步骤：按设备和日期计算告警数量" in content
