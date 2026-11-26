"""后门 OLS 的共享纯检查；不依赖模型框架、数据库或执行运行时。"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping

from .models import AteApplicabilityCheck, DataProfile, StandardizedGraph


ATE_REASON_SUMMARIES: dict[str, str] = {
    "ATE_GRAPH_HAS_UNDIRECTED_EDGE": "因果图包含未定向边，无法确定后门调整集。",
    "ATE_GRAPH_HAS_BIDIRECTED_EDGE": "因果图包含双箭头边，当前后门 OLS 不支持。",
    "ATE_GRAPH_HAS_UNSUPPORTED_EDGE": "因果图包含当前后门 OLS 不支持的边型。",
    "ATE_GRAPH_HAS_DIRECTED_CYCLE": "因果图包含有向环，无法进行后门调整估计。",
    "ATE_GRAPH_VARIABLE_MISSING": "因果图缺少处理变量或结果变量。",
    "ATE_GRAPH_VARIABLES_IDENTICAL": "处理变量与结果变量不能相同。",
    "ATE_GRAPH_TREATMENT_NOT_ANCESTOR": "处理变量不是结果变量的祖先，当前方法不适用。",
    "ATE_EXECUTION_COLUMN_MISSING": "数据缺少处理变量、结果变量或调整变量。",
    "ATE_EXECUTION_COLUMNS_INVALID": "估计所需数据列名称重复或不合法。",
    "ATE_VARIABLE_SEMANTICS_UNSUPPORTED": "处理变量或结果变量的测量语义不适用于当前后门 OLS。",
    "ATE_ADJUSTMENT_SEMANTICS_UNSUPPORTED": "调整变量的测量语义不适用于当前后门 OLS。",
    "ATE_EXECUTION_COLUMN_NOT_NUMERIC": "估计所用列必须为数值存储，不转换字符串数值或编码分类变量。",
    "ATE_EXECUTION_COLUMN_MISSING_VALUES": "估计所用列包含缺失值，当前方法不删除行或填值。",
    "ATE_EXECUTION_COLUMN_CONSTANT": "估计所用列包含常数列，当前方法不删除变量。",
    "ATE_EXECUTION_COLUMN_NON_FINITE": "估计所用列包含非有限值。",
    "ATE_DESIGN_MATRIX_SINGULAR": "设计矩阵奇异：处理变量与调整变量存在共线关系。",
    "ATE_INSUFFICIENT_DOF": "样本自由度不足，无法估计标准误和区间。",
}

ATE_REASON_CODES = frozenset(ATE_REASON_SUMMARIES)
ATE_GRAPH_REASON_CODES = frozenset(
    code for code in ATE_REASON_CODES if code.startswith("ATE_GRAPH_")
)


def check_ate_graph(
    *,
    graph: StandardizedGraph,
    graph_result_ref: str,
    treatment: str,
    outcome: str,
) -> AteApplicabilityCheck:
    """检查当前方法要求的 DAG，并按名称排序处理变量的全部父节点。

    调用者负责验证图引用的归属与源算法图契约。此处只检查方法适用性，
    不证明方向正确、无潜在混杂或线性假设成立。
    """

    def unavailable(reason_code: str) -> AteApplicabilityCheck:
        return AteApplicabilityCheck(
            graph_result_ref=graph_result_ref, treatment=treatment, outcome=outcome,
            adjustment_set=None, execution_columns=None,
            applicability="not_applicable", reason_code=reason_code,
        )

    edge_types = {edge.edge_type for edge in graph.edges}
    if "undirected" in edge_types:
        return unavailable("ATE_GRAPH_HAS_UNDIRECTED_EDGE")
    if "bidirected" in edge_types:
        return unavailable("ATE_GRAPH_HAS_BIDIRECTED_EDGE")
    if edge_types - {"directed"}:
        return unavailable("ATE_GRAPH_HAS_UNSUPPORTED_EDGE")
    if treatment not in graph.nodes or outcome not in graph.nodes:
        return unavailable("ATE_GRAPH_VARIABLE_MISSING")
    if treatment == outcome:
        return unavailable("ATE_GRAPH_VARIABLES_IDENTICAL")

    children: dict[str, set[str]] = {node: set() for node in graph.nodes}
    incoming = dict.fromkeys(graph.nodes, 0)
    for edge in graph.edges:
        if edge.target not in children[edge.source]:
            children[edge.source].add(edge.target)
            incoming[edge.target] += 1
    ready = deque(node for node, count in incoming.items() if count == 0)
    visited = 0
    while ready:
        node = ready.popleft()
        visited += 1
        for child in children[node]:
            incoming[child] -= 1
            if incoming[child] == 0:
                ready.append(child)
    if visited != len(graph.nodes):
        return unavailable("ATE_GRAPH_HAS_DIRECTED_CYCLE")

    descendants: set[str] = set()
    stack = [treatment]
    while stack:
        for child in children[stack.pop()]:
            if child not in descendants:
                descendants.add(child)
                stack.append(child)
    if outcome not in descendants:
        return unavailable("ATE_GRAPH_TREATMENT_NOT_ANCESTOR")

    adjustment = tuple(sorted({e.source for e in graph.edges if e.target == treatment}))
    return AteApplicabilityCheck(
        graph_result_ref=graph_result_ref, treatment=treatment, outcome=outcome,
        adjustment_set=adjustment, execution_columns=(treatment, outcome, *adjustment),
        applicability="ready", reason_code=None,
    )


def check_ate_profile(
    check: AteApplicabilityCheck,
    *,
    profile: DataProfile,
    semantic_kinds: Mapping[str, str] | None,
) -> AteApplicabilityCheck:
    """仅检查实际估计列；未声明语义不根据整数类型或唯一值数量推断。"""

    if check.applicability != "ready":
        return check
    selected = set(check.execution_columns)
    reason = None
    if len(set(profile.column_names)) != len(profile.column_names):
        reason = "ATE_EXECUTION_COLUMNS_INVALID"
    elif selected - set(profile.column_names):
        reason = "ATE_EXECUTION_COLUMN_MISSING"
    else:
        unsupported = {
            column for column, kind in (semantic_kinds or {}).items()
            if column in selected and kind in {"category", "ordinal", "binary"}
        }
        if unsupported & {check.treatment, check.outcome}:
            reason = "ATE_VARIABLE_SEMANTICS_UNSUPPORTED"
        elif unsupported:
            reason = "ATE_ADJUSTMENT_SEMANTICS_UNSUPPORTED"
        elif selected & (set(profile.missing_columns) | set(profile.empty_columns)):
            reason = "ATE_EXECUTION_COLUMN_MISSING_VALUES"
        elif selected & set(profile.non_finite_columns):
            reason = "ATE_EXECUTION_COLUMN_NON_FINITE"
        elif selected & set(profile.constant_columns):
            reason = "ATE_EXECUTION_COLUMN_CONSTANT"
        elif selected - set(profile.numeric_columns) or selected & set(profile.categorical_columns):
            reason = "ATE_EXECUTION_COLUMN_NOT_NUMERIC"
        elif profile.row_count <= 2 + len(check.adjustment_set):
            reason = "ATE_INSUFFICIENT_DOF"
    if reason is None:
        return check
    return AteApplicabilityCheck.model_validate(
        check.model_dump() | {"applicability": "not_applicable", "reason_code": reason}
    )
