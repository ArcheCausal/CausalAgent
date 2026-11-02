"""依据数据集画像回答交互，不编造领域知识。"""
from __future__ import annotations

QUESTION = "data_processing_question"
PLAN_CONFIRMATION = "data_processing_plan_confirmation"
DESTRUCTIVE_KEYWORDS = (
    "drop", "delete", "删除", "删行", "删列", "rename", "重命名",
    "aggregate", "聚合", "filter", "筛选", "新增", "mutate",
)


def _destructive(transformations) -> list[str]:
    items = transformations if isinstance(transformations, (list, tuple)) else (transformations,)
    hits = []
    for transformation in items:
        lowered = str(transformation).lower()
        if any(keyword in lowered for keyword in DESTRUCTIVE_KEYWORDS):
            hits.append(str(transformation))
    return hits


def plan_is_safe(interaction: dict, dim: int) -> tuple[bool, str]:
    plan = interaction.get("plan") if isinstance(interaction, dict) else None
    if not isinstance(plan, dict):
        return False, "plan 缺失或格式无效"
    semantics = plan.get("variable_semantics") or {}
    if not isinstance(semantics, dict):
        return False, "variable_semantics 格式无效"
    missing = {f"X{index}" for index in range(dim)} - set(semantics)
    if missing:
        return False, "variable_semantics 未覆盖变量 " + ",".join(sorted(missing))
    hits = _destructive(plan.get("transformations") or ())
    if hits:
        return False, "包含破坏性变换 " + ",".join(hits)
    return True, ""


def question_text(profile: dict) -> str:
    dim = profile["n_cols"]
    kinds = profile["kinds"]
    missing = "存在缺失值" if profile["missing"] else "无缺失值"
    kind_text = "，".join(
        f"X{index}={'二值' if kinds[index] == 'binary' else '数值'}"
        for index in range(dim)
    )
    ranges = "；".join(
        f"X{index}∈[{low:.4g}, {high:.4g}]，{unique} 个唯一值"
        for index, ((low, high), unique) in enumerate(zip(profile["ranges"], profile["uniques"]))
    )
    return (
        f"这是匿名观测数据：{profile['n_rows']} 行 × {dim} 列，{missing}。"
        f"{kind_text}。{ranges}。"
        f"这些是匿名观测变量 X0..X{dim - 1}，目标是恢复变量间因果结构；"
        "请按数据本身推断，不要依赖领域先验。"
    )


def answer_for(interrupt: dict, profile: dict) -> tuple[str | dict, str]:
    interaction = interrupt.get("interaction") if isinstance(interrupt, dict) else None
    if not isinstance(interaction, dict):
        interaction = {}
    interaction_type = interaction.get("interaction_type") or ""
    if interaction_type == PLAN_CONFIRMATION:
        safe, reason = plan_is_safe(interaction, profile["n_cols"])
        if safe:
            return {"action": "confirm", "plan_version": interaction.get("plan_version")}, "confirm"
        return (
            f"不能确认该方案：{reason}。不得删除、筛选或聚合数据；请保留全部变量与全部行，只允许标准化、缩放或编码。",
            "reject",
        )
    return question_text(profile), "profile"
