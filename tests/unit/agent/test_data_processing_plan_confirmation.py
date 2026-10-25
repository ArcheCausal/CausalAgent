"""数据处理方案版本与用户确认的纯契约测试。"""

from Agent.data_processing.plans import (
    PLAN_CONFIRMATION_INTERACTION_TYPE,
    build_processing_plan_version,
    extend_data_processing_semantic_identity,
    plan_confirmation_matches,
    plan_confirmation_version,
)
from Agent.deep_agent_tools.interaction import (
    DataProcessingPlanConfirmationRequest,
    DataProcessingPlanSummary,
)


JOB_ID = "00000000-0000-0000-0000-000000000901"
SOURCE_HASH = "a" * 64
SEMANTIC_IDENTITY = "b" * 64
METADATA = {
    "summary": "按设备和日期统计告警数。",
    "observation_unit": "设备-日期",
    "transformations": ["按设备和日期分组并计数"],
    "variable_semantics": {"alarm_count": "该设备当天的告警次数"},
    "limitations": ["不包含未触发告警的设备日期"],
}


def _version(**overrides: object) -> str:
    values: dict[str, object] = {
        "job_id": JOB_ID,
        "source_hash": SOURCE_HASH,
        "semantic_identity": SEMANTIC_IDENTITY,
        "metadata": METADATA,
    }
    values.update(overrides)
    return build_processing_plan_version(**values)  # type: ignore[arg-type]


def test_plan_version_binds_job_input_semantics_and_description() -> None:
    original = _version()

    assert original == _version()
    assert original != _version(job_id="00000000-0000-0000-0000-000000000902")
    assert original != _version(source_hash="c" * 64)
    assert original != _version(semantic_identity="d" * 64)
    assert original != _version(metadata={**METADATA, "summary": "不同处理方案。"})


def test_user_semantic_answer_invalidates_an_earlier_plan_version() -> None:
    revised_identity = extend_data_processing_semantic_identity(
        SEMANTIC_IDENTITY,
        "按每天而不是每小时统计告警。",
    )

    assert revised_identity != SEMANTIC_IDENTITY
    assert _version(semantic_identity=revised_identity) != _version()


def test_only_the_exact_current_plan_version_is_an_authorization() -> None:
    version = _version()
    interaction = {
        "interaction_type": PLAN_CONFIRMATION_INTERACTION_TYPE,
        "plan_version": version,
    }

    assert plan_confirmation_matches(
        interaction,
        {"action": "confirm", "plan_version": version},
    ) is True
    assert plan_confirmation_matches(
        interaction,
        {"action": "confirm", "plan_version": "e" * 64},
    ) is False
    assert plan_confirmation_matches(interaction, "拒绝当前方案") is None
    assert plan_confirmation_matches(
        {"interaction_type": "data_processing_question"},
        {"action": "confirm", "plan_version": version},
    ) is None
    assert plan_confirmation_version(
        '{"action":"confirm","plan_version":"' + version + '"}'
    ) == version


def test_confirmation_payload_displays_plan_without_script_content() -> None:
    request = DataProcessingPlanConfirmationRequest(
        prompt="请检查方案。",
        question_id="q-plan-1",
        expires_at="2026-10-23T12:05:00Z",
        plan_version=_version(),
        plan=DataProcessingPlanSummary.model_validate(METADATA),
    )

    payload = request.public_payload()

    assert payload["interaction_type"] == PLAN_CONFIRMATION_INTERACTION_TYPE
    assert payload["plan_version"] == _version()
    assert payload["plan"]["observation_unit"] == "设备-日期"
    assert "script" not in payload["plan"]

    forwarded = DataProcessingPlanConfirmationRequest(
        prompt=payload["message"],
        question_id=payload["question_id"],
        expires_at=payload["expires_at"],
        plan_version=payload["plan_version"],
        plan=payload["plan"],
    )
    assert forwarded.public_payload() == payload
