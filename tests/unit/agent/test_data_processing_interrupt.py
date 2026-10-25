"""开放式语义问题与数据处理 checkpoint 身份契约。"""

from datetime import datetime, timezone

from Agent.deep_agent_tools.identity import build_data_processing_run_id
from Agent.deep_agent_tools.interaction import DataProcessingQuestionRequest


JOB_ID = "00000000-0000-0000-0000-000000000901"
SOURCE_HASH = "a" * 64
SEMANTIC_HASH = "b" * 64


def test_question_uses_the_existing_job_interaction_payload() -> None:
    request = DataProcessingQuestionRequest(
        prompt="请说明每台设备每天的告警记录应如何合并。",
        question_id="question-1",
        expires_at=datetime.now(timezone.utc).isoformat(),
    )

    payload = request.public_payload()

    assert payload == {
        "type": "interaction",
        "interaction_type": "data_processing_question",
        "question_id": "question-1",
        "message": "请说明每台设备每天的告警记录应如何合并。",
        "expires_at": request.expires_at,
        "allow_other": True,
    }


def test_processing_checkpoint_identity_changes_only_with_source() -> None:
    first = build_data_processing_run_id(
        job_id=JOB_ID,
        source_input_identity=SOURCE_HASH,
        semantic_identity=SEMANTIC_HASH,
    )

    assert first == build_data_processing_run_id(
        job_id=JOB_ID,
        source_input_identity=SOURCE_HASH,
        semantic_identity=SEMANTIC_HASH,
    )
    assert first != build_data_processing_run_id(
        job_id=JOB_ID,
        source_input_identity="c" * 64,
        semantic_identity=SEMANTIC_HASH,
    )
    assert first == build_data_processing_run_id(
        job_id=JOB_ID,
        source_input_identity=SOURCE_HASH,
        semantic_identity="d" * 64,
    )
