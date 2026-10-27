"""交互答复的统一判定：按 ``interaction_type`` 选择答复内容与幂等键。

文档职责：定义压测场景的研究语义、方案确认约束，以及未识别交互的缺口记录规则。
适用范围：普通负载、到达率场景与取消/恢复专项共用的答复流程；纯函数，不发起请求。

答复只依赖场景约束和当前事件载荷。方案版本取自事件本身；同一问题重试复用同一幂等键，
新问题使用新键。方案超出场景允许的处理范围时记录偏离，不自动确认。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import time
import uuid

QUESTION_TYPE = "data_processing_question"
PLAN_CONFIRMATION_TYPE = "data_processing_plan_confirmation"
ALGORITHM_OPTION_TYPES = {"data_processing_options", "algorithm_options"}
_PLAN_VERSION = re.compile(r"[0-9a-f]{64}")
_KEY_NAMESPACE = uuid.UUID("5b0e8f2c-3ad0-5f57-9d43-7c1f0a2b6e11")


@dataclass(frozen=True)
class ScenarioProfile:
    """一个压测场景对研究语义和处理方案的明确约束。"""

    name: str
    research_answer: str
    observation_unit: str
    expected_columns: tuple[str, ...] = ()
    requires_full_rows: bool = True
    allowed_transformations: tuple[str, ...] = ()
    forbidden_transformations: tuple[str, ...] = (
        "聚合", "汇总", "筛选", "删除列", "删列", "重命名", "标准化", "抽样", "新增字段",
        "aggregate", "group by", "drop", "rename", "normaliz", "standardiz", "sample",
    )
    max_rounds: int = 6
    max_interaction_seconds: float = 300.0


@dataclass
class InteractionLedger:
    """单个 Job 的交互轮次与总时间上限，以及问题到幂等键的映射。"""

    max_rounds: int
    max_seconds: float
    started_at: float = field(default_factory=time.monotonic)
    rounds: list[dict] = field(default_factory=list)
    keys: dict[str, str] = field(default_factory=dict)

    def exhausted(self) -> str | None:
        if len(self.rounds) >= self.max_rounds:
            return "interaction_round_limit"
        if time.monotonic() - self.started_at >= self.max_seconds:
            return "interaction_time_limit"
        return None

    def key_for(self, job_id: str, question_id: str) -> str:
        """同一问题重试复用原键；新问题按键空间生成新键。"""
        existing = self.keys.get(question_id)
        if existing is not None:
            return existing
        key = str(uuid.uuid5(_KEY_NAMESPACE, f"{job_id}:{question_id}"))
        self.keys[question_id] = key
        return key


@dataclass(frozen=True)
class Decision:
    """一次交互的答复决定；未识别或偏离时 ``answer`` 为空，不得发送。"""

    status: str
    interaction_type: str
    question_id: str
    answer: str
    plan_version: str | None = None
    key: str = ""
    deviation: tuple[str, ...] = ()
    detail: str = ""

    @property
    def sendable(self) -> bool:
        return self.status == "answered" and bool(self.answer)

    def record(self) -> dict:
        """写入生命周期账本的公开字段；不包含方案正文或脚本。"""
        return {"interaction_type": self.interaction_type or None,
                "interaction_status": self.status,
                "interaction_question_id": self.question_id or None,
                "plan_version": self.plan_version,
                "interaction_deviation": list(self.deviation),
                "interaction_detail": self.detail or None,
                "interaction_answered_at": time.time() if self.sendable else None,
                "resume_idempotency_key": self.key or None}


def interaction_type(payload: dict) -> str:
    """从 interrupt 事件读取交互类型；兼容类型写在 interaction.type 的旧载荷。"""
    interaction = payload.get("interaction")
    if not isinstance(interaction, dict):
        return ""
    value = interaction.get("interaction_type") or interaction.get("type")
    return value if isinstance(value, str) else ""


def question_id(payload: dict) -> str:
    interaction = payload.get("interaction") or {}
    value = payload.get("question_id") or (interaction.get("question_id") if isinstance(interaction, dict) else None)
    return value if isinstance(value, str) else ""


def _options(interaction: dict, profile: ScenarioProfile) -> list[dict]:
    options = [item for item in interaction.get("options") or []
               if isinstance(item, dict) and item.get("option_id")]
    if profile.name.startswith("cdfm"):
        options = [item for item in options if item.get("capability_id") == "causal.cdfm"]
    return options


def _plan_text(plan: dict) -> str:
    parts = [str(plan.get("summary") or ""), str(plan.get("observation_unit") or "")]
    for value in plan.get("transformations") or []:
        parts.append(str(value))
    return "\n".join(parts).lower()


def plan_deviations(plan: dict, profile: ScenarioProfile) -> tuple[str, ...]:
    """按场景约束核对方案；把意外聚合、删列或研究范围变化识别为偏离。"""
    deviations: list[str] = []
    if not isinstance(plan, dict):
        return ("plan_missing",)
    text = _plan_text(plan)
    for token in profile.forbidden_transformations:
        if token.lower() in text:
            deviations.append(f"forbidden_transformation:{token}")
    columns = plan.get("variable_semantics")
    if profile.expected_columns:
        if not isinstance(columns, dict) or set(columns) != set(profile.expected_columns):
            present = sorted(columns) if isinstance(columns, dict) else []
            deviations.append("variable_scope_changed:" + ",".join(present))
    observation = plan.get("observation_unit")
    if profile.observation_unit and (not isinstance(observation, str) or not observation.strip()):
        deviations.append("observation_unit_missing")
    return tuple(dict.fromkeys(deviations))


def decide(payload: dict, job_id: str, profile: ScenarioProfile,
           ledger: InteractionLedger) -> Decision:
    """按交互类型给出答复；未识别类型只记录缺口，不使用默认继续。"""
    kind, qid = interaction_type(payload), question_id(payload)
    interaction = payload.get("interaction") if isinstance(payload.get("interaction"), dict) else {}
    exhausted = ledger.exhausted()
    if exhausted:
        # 超限的请求也计入轮次，避免同一问题反复触发上限检查。
        ledger.rounds.append({"interaction_type": kind or None, "interaction_status": "budget_exhausted",
                              "interaction_question_id": qid or None})
        return Decision("budget_exhausted", kind, qid, "", key="", detail=exhausted)
    decision = _decide(kind, qid, interaction, profile, ledger, job_id)
    ledger.rounds.append(decision.record())
    return decision


def _decide(kind: str, qid: str, interaction: dict, profile: ScenarioProfile,
            ledger: InteractionLedger, job_id: str) -> Decision:
    """在轮次预算内选择答复；调用方负责登记本轮结果。"""
    if not qid:
        return Decision("unrecognized", kind, "", "", detail="missing_question_id")
    key = ledger.key_for(job_id, qid)
    options = _options(interaction, profile)
    if kind in ALGORITHM_OPTION_TYPES or (not kind and options):
        if not options:
            return Decision("unrecognized", kind or "options", qid, "", key=key,
                            detail="no_matching_algorithm_option")
        return Decision("answered", kind or "options", qid,
                        json.dumps({"option_id": options[0]["option_id"]}), key=key)
    if kind == QUESTION_TYPE:
        if not profile.research_answer:
            return Decision("unrecognized", kind, qid, "", key=key, detail="scenario_has_no_research_answer")
        return Decision("answered", kind, qid, profile.research_answer, key=key)
    if kind == PLAN_CONFIRMATION_TYPE:
        version = interaction.get("plan_version")
        if not isinstance(version, str) or _PLAN_VERSION.fullmatch(version) is None:
            return Decision("unrecognized", kind, qid, "", key=key, detail="invalid_plan_version")
        deviations = plan_deviations(interaction.get("plan") or {}, profile)
        if deviations:
            return Decision("deviation", kind, qid, "", plan_version=version, key=key,
                            deviation=deviations, detail="plan_outside_scenario_scope")
        answer = json.dumps({"action": "confirm", "plan_version": version})
        return Decision("answered", kind, qid, answer, plan_version=version, key=key)
    return Decision("unrecognized", kind or "empty", qid, "", key=key, detail="unsupported_interaction_type")


def profile_from_spec(spec: dict) -> ScenarioProfile:
    """从档位规格构造场景约束；缺字段时使用保守默认值。"""
    columns = spec.get("expected_columns") or ()
    allowed = spec.get("allowed_transformations") or ()
    return ScenarioProfile(
        name=str(spec.get("mode") or ""),
        research_answer=str(spec.get("research_answer") or ""),
        observation_unit=str(spec.get("observation_unit") or ""),
        expected_columns=tuple(str(item) for item in columns),
        requires_full_rows=bool(spec.get("requires_full_rows", True)),
        allowed_transformations=tuple(str(item) for item in allowed),
        max_rounds=int(spec.get("max_interactions", 6)),
        max_interaction_seconds=float(spec.get("interaction_deadline_seconds", 300)),
    )
