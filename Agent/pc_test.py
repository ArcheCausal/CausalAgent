"""PC 独立性检验的测量语义选择规则，不从存储 dtype 推断测量含义。"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from Agent.measurement_semantics import normalize_semantic_kinds


# 计数的保守自动适用边界，不代表条件列联表的期望频数已经验证。
MAX_PC_COUNT_LEVELS = 20


@dataclass(frozen=True)
class PcTestResolution:
    requested_test: str
    independence_test: str | None
    selection_reason: str

    def diagnostics(self) -> dict[str, str | None]:
        return {"requested_test": self.requested_test,
                "independence_test": self.independence_test,
                "selection_reason": self.selection_reason}


def resolve_pc_test(
    *, columns: Sequence[str], semantic_kinds: Mapping[str, str] | None,
    test: str = "auto", count_cardinalities: Mapping[str, int] | None = None,
) -> PcTestResolution:
    """auto 与显式检验使用同一相容规则；未满足时不选择替代检验。"""
    if test not in {"auto", "fisherz", "chisq"}:
        raise ValueError("unsupported PC independence test")
    kinds = normalize_semantic_kinds(semantic_kinds or {}, columns=columns)
    observed = {kinds.get(column, "unknown") for column in columns}
    selected = None
    if not columns or "unknown" in observed:
        reason = "参与分析的变量存在未知测量类型，不能按数值 dtype 选择检验。"
    elif observed == {"continuous"}:
        selected = "fisherz"
        reason = "全部变量声明为连续；采用线性高斯工作假设，该假设尚未验证。"
    elif "continuous" in observed:
        reason = "当前 PC 检验不自动支持连续与离散变量混合。"
    else:
        counts = [column for column in columns if kinds[column] == "count"]
        if counts and (count_cardinalities is None or any(
            column not in count_cardinalities or count_cardinalities[column] > MAX_PC_COUNT_LEVELS
            for column in counts
        )):
            reason = f"计数变量基数未知或超过保守上限 {MAX_PC_COUNT_LEVELS}，不能直接采用卡方检验。"
        else:
            selected = "chisq"
            reason = "全部变量声明为二值、分类、有序或低基数计数；采用离散卡方检验，未验证条件列联表的频数假设。"
    if selected is not None and test not in {"auto", selected}:
        selected = None
        reason = "显式指定的检验与实际参与分析的变量测量类型不相容。"
    return PcTestResolution(test, selected, reason)
