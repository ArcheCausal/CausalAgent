from Agent.data_processing.validation import validate_candidate


def test_candidate_cell_limit_is_checked_before_dataframe_parsing() -> None:
    report = validate_candidate(
        data_bytes=b"device,day\n1,2026-01-01\n2,2026-01-02\n",
        script_bytes=b"print('prepared')\n",
        metadata={},
        analysis_parameters={},
        specifications=(),
        max_bytes=1024,
        max_cells=3,
    )

    assert report["accepted"] is False
    assert report["errors"] == [
        {
            "code": "cell_count_limit_exceeded",
            "message": "CSV 数据单元格总数超过独立校验限制",
        }
    ]
