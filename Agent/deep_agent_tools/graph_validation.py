"""标准化入口与主结果门禁共用的图结构检查。"""

from collections import deque

from .models import GraphValidation, StandardizedGraph


_ALLOWED_EDGES = {
    "pdag": {"directed", "undirected"},
    "dag_target_to_source": {"directed"},
    "acyclic_directed_mixed_graph": {"directed", "bidirected"},
    "latent_mixed_graph": {"directed", "bidirected", "undirected", "partially_directed"},
}


def validate_graph_structure(graph: StandardizedGraph, *, graph_semantics: str) -> GraphValidation:
    """保留每条边，只检查声明图类型的结构约束；不证明统计假设。"""
    violations: list[str] = []
    allowed = _ALLOWED_EDGES.get(graph_semantics)
    if allowed is None:
        violations.append("unsupported_graph_semantics")
    if graph.graph_semantics != graph_semantics:
        violations.append("graph_semantics_mismatch")
    adjacency: dict[str, list[str]] = {node: [] for node in graph.nodes}
    incoming = dict.fromkeys(graph.nodes, 0)
    seen: set[tuple[str, str, str]] = set()
    pairs: dict[tuple[str, str], set[str]] = {}
    for edge in graph.edges:
        if allowed is not None and edge.edge_type not in allowed:
            violations.append("edge_type_not_allowed")
        if edge.source == edge.target:
            violations.append("self_loop")
        ends = (edge.source, edge.target) if edge.edge_type == "directed" else tuple(sorted((edge.source, edge.target)))
        key = (*ends, edge.edge_type)
        if key in seen:
            violations.append("duplicate_edge")
        seen.add(key)
        pair = tuple(sorted((edge.source, edge.target)))
        pairs.setdefault(pair, set()).add(edge.edge_type)
        if edge.edge_type == "directed":
            adjacency[edge.source].append(edge.target)
            incoming[edge.target] += 1
    ready = deque(node for node, count in incoming.items() if count == 0)
    visited = 0
    while ready:
        node = ready.popleft()
        visited += 1
        for target in adjacency[node]:
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
    if visited != len(graph.nodes):
        violations.append("directed_cycle")
    if graph_semantics == "acyclic_directed_mixed_graph" and any(
        {"directed", "bidirected"} <= types for types in pairs.values()
    ):
        violations.append("bow_not_allowed")
    return GraphValidation(
        valid=not violations, violations=sorted(set(violations)),
        checks_performed=["graph_type", "edge_types", "self_loops", "duplicate_edges", "directed_acyclicity"],
        not_checked=["causal_identifiability", "statistical_assumptions", "edge_accuracy"],
    )
